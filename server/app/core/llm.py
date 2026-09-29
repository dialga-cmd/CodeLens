"""Shared LLM client for CodeLens, backed by the Nebius Token Factory.

Token Factory is OpenAI-compatible, so this is the standard `openai` SDK pointed at
`https://api.tokenfactory.nebius.com/v1/`. Everything the application needs from a
model lives in this one module:

* two roles, chosen by environment variable - a **heavy** model for reasoning-heavy
  work (security triage, architecture, fix proposals) and a **fast** model for
  high-volume work (chat, streaming, per-file triage);
* bounded retries with exponential backoff and jitter that honours `Retry-After`,
  because Token Factory rate limits are dynamic and return HTTP 429;
* explicit timeouts and a small concurrency gate, so a wide fan-out degrades
  instead of tripping the rate limiter;
* JSON-mode calls with a parse-and-repair path, since a model can still return
  text that is not valid JSON;
* a tool loop for the chat assistant.

Nothing here prints or logs the API key, and every error message is scrubbed
before it can reach a log line or an HTTP response.

Defaults come from the official model catalog; see `docs/PLATFORM_NOTES.md`.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Sequence

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    OpenAI,
    RateLimitError,
)

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_HEAVY_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"
DEFAULT_FAST_MODEL = "nvidia/Nemotron-3_5-Lightning"

ROLE_HEAVY = "heavy"
ROLE_FAST = "fast"

_JSON_ONLY_INSTRUCTION = (
    "Respond with a single valid JSON value and nothing else. "
    "Do not wrap it in markdown, do not add commentary, and do not add trailing commas."
)


def with_json_instruction(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``messages`` with the JSON-only instruction folded into the system turn."""
    prepared = [dict(message) for message in messages]
    if prepared and prepared[0].get("role") == "system" and isinstance(prepared[0].get("content"), str):
        prepared[0]["content"] = f"{prepared[0]['content'].rstrip()}\n\n{_JSON_ONLY_INSTRUCTION}"
    else:
        prepared.insert(0, {"role": "system", "content": _JSON_ONLY_INSTRUCTION})
    return prepared


class LLMError(RuntimeError):
    """Raised when a model call cannot be completed."""


# --------------------------------------------------------------------------- #
# configuration helpers
# --------------------------------------------------------------------------- #


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# JSON helpers (pure functions, unit tested in tests/test_llm.py)
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json|JSON|javascript|js)?\s*(.*?)```", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Return the content of the first fenced block, or the text unchanged."""
    match = _FENCE_RE.search(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip()


def _scan_balanced(text: str, start: int) -> str | None:
    """Return the first balanced ``{...}``/``[...]`` slice starting at ``start``.

    String literals and escapes are respected so that braces inside quoted values
    do not terminate the scan early.
    """
    opener = text[start]
    closer = {"{": "}", "[": "]"}[opener]
    depth = 0
    in_string = False
    quote = ""
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue

        if char in "\"'":
            in_string = True
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def repair_json(candidate: str) -> str:
    """Apply conservative repairs to a JSON document a model got almost right."""
    repaired = candidate
    # Smart quotes that some models emit instead of ASCII quotes.
    repaired = repaired.replace("“", '"').replace("”", '"').replace("’", "'")
    # Python literals.
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)
    repaired = re.sub(r"\bNone\b", "null", repaired)
    # Trailing commas before a closing bracket or brace.
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    # Python comments, which are not valid JSON.
    repaired = re.sub(r"(?m)//[^\n\"]*$", "", repaired)
    return repaired.strip()


def extract_json(text: str) -> Any:
    """Parse JSON out of a model response.

    Raises ``ValueError`` when no JSON value can be recovered, so callers can
    decide whether to retry or to degrade.
    """
    if not text or not text.strip():
        raise ValueError("empty model response")

    candidates = [text.strip()]

    unfenced = strip_code_fences(text)
    if unfenced and unfenced not in candidates:
        candidates.append(unfenced)

    for opener in ("{", "["):
        start = unfenced.find(opener)
        if start == -1:
            continue
        sliced = _scan_balanced(unfenced, start)
        if sliced:
            candidates.append(sliced)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        for attempt in (candidate, repair_json(candidate)):
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, ValueError):
                continue

    raise ValueError("model response contained no parsable JSON")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #


@dataclass
class ChatResult:
    """A completed model call, with the numbers needed for honest reporting."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    attempts: int = 1
    finish_reason: str = ""
    refused: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def usage_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_s": round(self.latency_s, 3),
            "attempts": self.attempts,
        }


@dataclass
class ToolCallRecord:
    """One tool invocation the assistant made while answering a question."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "result_preview": self.result_preview,
        }


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #


class LLMClient:
    """One place that talks to Token Factory."""

    def __init__(self) -> None:
        self.api_key: str = _env_str("NEBIUS_API_KEY")
        self.base_url: str = _env_str("NEBIUS_BASE_URL", DEFAULT_BASE_URL)
        self.heavy_model: str = _env_str("NEBIUS_MODEL_HEAVY", DEFAULT_HEAVY_MODEL)
        self.fast_model: str = _env_str("NEBIUS_MODEL_FAST", DEFAULT_FAST_MODEL)

        self.timeout: float = _env_float("NEBIUS_REQUEST_TIMEOUT", 120.0)
        self.connect_timeout: float = _env_float("NEBIUS_CONNECT_TIMEOUT", 15.0)
        self.max_retries: int = max(0, _env_int("NEBIUS_MAX_RETRIES", 3))
        self.max_output_tokens: int = max(256, _env_int("NEBIUS_MAX_OUTPUT_TOKENS", 4000))
        self.max_concurrency: int = max(1, _env_int("NEBIUS_MAX_CONCURRENCY", 4))
        self.json_repair_attempts: int = max(1, _env_int("NEBIUS_JSON_REPAIR_ATTEMPTS", 2))
        self.use_json_mode: bool = _env_bool("NEBIUS_USE_JSON_MODE", True)

        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None
        self._client_lock = threading.Lock()
        self._async_semaphore: asyncio.Semaphore | None = None
        self._sync_semaphore = threading.BoundedSemaphore(self.max_concurrency)

    # -- configuration ----------------------------------------------------- #

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def model_for(self, role: str) -> str:
        return self.heavy_model if role == ROLE_HEAVY else self.fast_model

    def describe(self) -> dict[str, Any]:
        """Safe-to-expose configuration: no key, no headers, no full request."""
        return {
            "configured": self.configured,
            "base_url": self.base_url,
            "models": {
                ROLE_HEAVY: self.heavy_model,
                ROLE_FAST: self.fast_model,
            },
            "max_output_tokens": self.max_output_tokens,
            "max_concurrency": self.max_concurrency,
            "max_retries": self.max_retries,
            "request_timeout": self.timeout,
        }

    def _redact(self, message: str) -> str:
        """Remove the key from anything that will be logged or returned."""
        text = str(message or "")
        if self.api_key:
            text = text.replace(self.api_key, "***redacted***")
        # Also drop anything that looks like a bearer token.
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}", "Bearer ***redacted***", text)
        return text

    # -- clients ----------------------------------------------------------- #

    def _timeout(self):
        from openai import Timeout

        return Timeout(connect=self.connect_timeout, read=self.timeout, write=self.timeout, pool=self.connect_timeout)

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    if not self.configured:
                        raise LLMError("NEBIUS_API_KEY is not set")
                    self._client = OpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url,
                        timeout=self._timeout(),
                        max_retries=0,  # retries are handled here so backoff is visible
                    )
        return self._client

    @property
    def async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            if not self.configured:
                raise LLMError("NEBIUS_API_KEY is not set")
            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self._timeout(),
                max_retries=0,
            )
        return self._async_client

    @property
    def async_semaphore(self) -> asyncio.Semaphore:
        # Created lazily so it binds to the running loop, not to import time.
        if self._async_semaphore is None:
            self._async_semaphore = asyncio.Semaphore(self.max_concurrency)
        return self._async_semaphore

    # -- retry policy ------------------------------------------------------ #

    def _retry_after_seconds(self, error: BaseException) -> float | None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None

    def _backoff_seconds(self, attempt: int, error: BaseException | None) -> float:
        retry_after = self._retry_after_seconds(error) if error is not None else None
        if retry_after is not None:
            return min(retry_after, 30.0)
        # Exponential backoff with full jitter, capped at 8 seconds.
        window = min(0.75 * (2**attempt), 8.0)
        return random.uniform(0.0, window)

    def _is_retryable(self, error: BaseException) -> bool:
        if isinstance(error, (RateLimitError, APIConnectionError)):
            return True
        if isinstance(error, APIStatusError):
            status = getattr(error, "status_code", 0) or 0
            return status >= 500 or status == 429
        return isinstance(error, (TimeoutError, asyncio.TimeoutError))

    def _failure_message(self, error: BaseException) -> str:
        if isinstance(error, LLMError):
            return self._redact(str(error))
        status = getattr(error, "status_code", None)
        prefix = f"Token Factory returned HTTP {status}: " if status else "Token Factory request failed: "
        return self._redact(f"{prefix}{error}")

    def _request_kwargs(
        self,
        role: str,
        model: str | None,
        messages: Sequence[dict[str, Any]],
        temperature: float | None,
        max_output_tokens: int | None,
        response_format: dict[str, Any] | None,
        tools: Sequence[dict[str, Any]] | None,
        tool_choice: Any | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model or self.model_for(role),
            "messages": list(messages),
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        limit = max_output_tokens or self.max_output_tokens
        if limit:
            kwargs["max_tokens"] = limit
        if response_format:
            kwargs["response_format"] = response_format
        if tools:
            kwargs["tools"] = list(tools)
            kwargs["tool_choice"] = tool_choice or "auto"
        if extra:
            kwargs.update(extra)
        return kwargs

    @staticmethod
    def _result_from_response(response: Any, model: str, started: float, attempts: int) -> ChatResult:
        choice = response.choices[0] if getattr(response, "choices", None) else None
        message = getattr(choice, "message", None) if choice is not None else None
        content = getattr(message, "content", None) if message is not None else None
        usage = getattr(response, "usage", None)
        return ChatResult(
            text=content or "",
            model=getattr(response, "model", None) or model,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
            latency_s=time.perf_counter() - started,
            attempts=attempts,
            finish_reason=getattr(choice, "finish_reason", "") or "",
            refused=bool(getattr(message, "refusal", None)) if message is not None else False,
        )

    # -- sync calls (used by the analysis pipeline, which runs in a thread) -- #

    def chat_sync(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        role: str = ROLE_HEAVY,
        model: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResult:
        kwargs = self._request_kwargs(role, model, messages, temperature, max_output_tokens, response_format, None, None, extra)
        last_error: BaseException | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with self._sync_semaphore:
                    started = time.perf_counter()
                    response = self.client.chat.completions.create(**kwargs)
                return self._result_from_response(response, kwargs["model"], started, attempt + 1)
            except Exception as error:  # noqa: BLE001 - re-raised below
                last_error = error
                if not self._is_retryable(error) or attempt >= self.max_retries:
                    break
                time.sleep(self._backoff_seconds(attempt, error))

        raise LLMError(self._failure_message(last_error)) from last_error

    def chat_json_sync(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        role: str = ROLE_HEAVY,
        model: str | None = None,
        temperature: float = 0.1,
        max_output_tokens: int | None = None,
        schema: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[Any, ChatResult]:
        return self._json_sync(
            messages,
            role=role,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            schema=schema,
            extra=extra,
        )

    def _json_sync(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        role: str,
        model: str | None,
        temperature: float,
        max_output_tokens: int | None,
        schema: dict[str, Any] | None,
        extra: dict[str, Any] | None,
    ) -> tuple[Any, ChatResult]:
        base = with_json_instruction(messages)

        response_format: dict[str, Any] | None = None
        if self.use_json_mode:
            if schema:
                response_format = {"type": "json_schema", "json_schema": schema}
            else:
                response_format = {"type": "json_object"}

        last_error: str | None = None
        result: ChatResult | None = None

        for attempt in range(self.json_repair_attempts):
            try:
                result = self.chat_sync(
                    base,
                    role=role,
                    model=model,
                    temperature=temperature if attempt == 0 else 0.0,
                    max_output_tokens=max_output_tokens,
                    response_format=response_format,
                    extra=extra,
                )
            except LLMError as error:
                # A rejected response_format should not cost the whole answer.
                if "response_format" in str(error) and response_format is not None:
                    response_format = None
                    last_error = str(error)
                    continue
                raise

            if result.refused and not result.text.strip():
                raise LLMError("the model declined to answer this request")

            try:
                return extract_json(result.text), result
            except ValueError as error:
                last_error = str(error)
                # Drop structured-output mode and ask for raw JSON instead.
                response_format = None

        raise LLMError(f"model did not return valid JSON after {self.json_repair_attempts} attempts: {last_error}")

    # -- async calls (used by the HTTP layer) ------------------------------- #

    async def _acomplete(self, kwargs: dict[str, Any]) -> tuple[Any, float, int]:
        """Call Token Factory with retries. Returns the raw response object."""
        last_error: BaseException | None = None

        for attempt in range(self.max_retries + 1):
            try:
                async with self.async_semaphore:
                    started = time.perf_counter()
                    response = await self.async_client.chat.completions.create(**kwargs)
                return response, started, attempt + 1
            except Exception as error:  # noqa: BLE001 - re-raised below
                last_error = error
                if not self._is_retryable(error) or attempt >= self.max_retries:
                    break
                await asyncio.sleep(self._backoff_seconds(attempt, error))

        raise LLMError(self._failure_message(last_error)) from last_error

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        role: str = ROLE_FAST,
        model: str | None = None,
        temperature: float = 0.3,
        max_output_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResult:
        kwargs = self._request_kwargs(role, model, messages, temperature, max_output_tokens, response_format, None, None, extra)
        response, started, attempts = await self._acomplete(kwargs)
        return self._result_from_response(response, kwargs["model"], started, attempts)

    async def chat_json(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        role: str = ROLE_HEAVY,
        model: str | None = None,
        temperature: float = 0.1,
        max_output_tokens: int | None = None,
        schema: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[Any, ChatResult]:
        base = with_json_instruction(messages)

        response_format: dict[str, Any] | None = None
        if self.use_json_mode:
            response_format = {"type": "json_schema", "json_schema": schema} if schema else {"type": "json_object"}

        last_error: str | None = None

        for attempt in range(self.json_repair_attempts):
            try:
                result = await self.chat(
                    base,
                    role=role,
                    model=model,
                    temperature=temperature if attempt == 0 else 0.0,
                    max_output_tokens=max_output_tokens,
                    response_format=response_format,
                    extra=extra,
                )
            except LLMError as error:
                if "response_format" in str(error) and response_format is not None:
                    response_format = None
                    last_error = str(error)
                    continue
                raise

            if result.refused and not result.text.strip():
                raise LLMError("the model declined to answer this request")

            try:
                return extract_json(result.text), result
            except ValueError as error:
                last_error = str(error)
                response_format = None

        raise LLMError(f"model did not return valid JSON after {self.json_repair_attempts} attempts: {last_error}")

    async def chat_stream(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        role: str = ROLE_FAST,
        model: str | None = None,
        temperature: float = 0.3,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Retries restart the stream, so partial output is only
        forwarded once a chunk actually arrives on the successful attempt."""

        async def _stream() -> AsyncIterator[str]:
            async with self.async_semaphore:
                async with self.async_client.chat.completions.stream(
                    model=model or self.model_for(role),
                    messages=list(messages),
                    temperature=temperature,
                    max_tokens=max_output_tokens or self.max_output_tokens,
                ) as stream:
                    async for chunk in stream:
                        delta = chunk.choices[0].delta if chunk.choices else None
                        content = getattr(delta, "content", None) if delta is not None else None
                        if content:
                            yield content

        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            produced = False
            try:
                async for piece in _stream():
                    produced = True
                    yield piece
                return
            except Exception as error:  # noqa: BLE001 - re-raised below
                last_error = error
                # A stream that already emitted cannot be safely resumed.
                if produced or not self._is_retryable(error) or attempt >= self.max_retries:
                    break
                await asyncio.sleep(self._backoff_seconds(attempt, error))

        raise LLMError(self._failure_message(last_error))

    async def chat_with_tools(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        execute: Callable[[str, dict[str, Any]], Any],
        *,
        role: str = ROLE_FAST,
        model: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int | None = None,
        max_rounds: int = 6,
    ) -> tuple[ChatResult, list[ToolCallRecord]]:
        """Run a bounded tool loop.

        ``execute`` receives the tool name and already-parsed arguments and returns
        either a string or a JSON-serialisable object. Its return value is sent back
        to the model as the tool result.
        """

        conversation: list[dict[str, Any]] = list(messages)
        records: list[ToolCallRecord] = []
        result: ChatResult | None = None

        for _ in range(max(1, max_rounds)):
            kwargs = self._request_kwargs(
                role,
                model,
                conversation,
                temperature,
                max_output_tokens,
                None,
                tools,
                "auto",
                None,
            )
            response, started, attempts = await self._acomplete(kwargs)
            result = self._result_from_response(response, kwargs["model"], started, attempts)

            choice = response.choices[0] if getattr(response, "choices", None) else None
            assistant_message = getattr(choice, "message", None) if choice is not None else None
            tool_calls = list(getattr(assistant_message, "tool_calls", None) or [])

            if not tool_calls:
                break

            # Mirror the assistant turn, then append one tool result per call.
            conversation.append(
                {
                    "role": "assistant",
                    "content": result.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.function.name, "arguments": call.function.arguments},
                        }
                        for call in tool_calls
                    ],
                }
            )

            for call in tool_calls:
                name = call.function.name
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {"input": arguments}

                try:
                    outcome = await execute(name, arguments) if asyncio.iscoroutinefunction(execute) else execute(name, arguments)
                except Exception as error:  # noqa: BLE001 - reported back to the model
                    outcome = {"error": self._redact(str(error))}

                payload = outcome if isinstance(outcome, str) else json.dumps(outcome, default=str)
                records.append(
                    ToolCallRecord(
                        name=name,
                        arguments=arguments,
                        result_preview=payload[:400],
                    )
                )
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": payload[:20000],
                    }
                )
        else:
            # Loop budget exhausted: ask once more, without tools, for a wrap-up.
            result = await self.chat(conversation, role=role, model=model, temperature=temperature)

        if result is None:
            raise LLMError("tool loop ended without a final answer")

        return result, records

    # -- model discovery ---------------------------------------------------- #

    def list_models(self) -> list[dict[str, Any]]:
        """List model IDs available to this account. The authority, not the catalog."""
        payload = self.client.models.list()
        models: list[dict[str, Any]] = []
        for entry in getattr(payload, "data", None) or []:
            models.append(
                {
                    "id": getattr(entry, "id", ""),
                    "name": getattr(entry, "name", "") or getattr(entry, "id", ""),
                    "context_length": getattr(entry, "context_length", None),
                    "status": getattr(entry, "status", None),
                    "description": getattr(entry, "description", None),
                }
            )
        models.sort(key=lambda item: item["id"])
        return models


_shared_client: LLMClient | None = None
_shared_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """Return the process-wide client, built from the environment on first use."""
    global _shared_client
    if _shared_client is None:
        with _shared_lock:
            if _shared_client is None:
                _shared_client = LLMClient()
    return _shared_client


def reset_llm_client() -> None:
    """Drop the cached client. Used by tests and after an environment change."""
    global _shared_client
    with _shared_lock:
        _shared_client = None
