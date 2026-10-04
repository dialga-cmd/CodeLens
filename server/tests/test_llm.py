"""The LLM layer: parsing, redaction and configuration.

Everything here is a pure function or a locally constructed client. No test in
this file opens a socket, so the suite passes without a ``NEBIUS_API_KEY`` and
CI needs no credentials.

The JSON helpers matter more than they look. Every verdict in the report is a
model response parsed into a structure, and a response that is 99% right must
not become a 500 for the user - so the repair path is pinned against the exact
mistakes models actually make: fences, prose around the object, smart quotes,
Python literals, trailing commas and comments.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.llm import (
    DEFAULT_BASE_URL,
    DEFAULT_FAST_MODEL,
    DEFAULT_HEAVY_MODEL,
    ROLE_FAST,
    ROLE_HEAVY,
    LLMClient,
    extract_json,
    repair_json,
    strip_code_fences,
    with_json_instruction,
)


# --------------------------------------------------------------------------- #
# strip_code_fences
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("language", ["", "json", "JSON", "javascript", "js"])
def test_a_fenced_block_yields_its_contents(language: str):
    text = f"Here you go:\n```{language}\n{{\"a\": 1}}\n```\nHope that helps."
    assert strip_code_fences(text) == '{"a": 1}'


def test_unfenced_text_comes_back_stripped():
    assert strip_code_fences("  {\"a\": 1}  ") == '{"a": 1}'
    assert strip_code_fences("") == ""


def test_the_first_block_wins():
    text = '```json\n{"first": true}\n```\nand then\n```json\n{"second": true}\n```'
    assert strip_code_fences(text) == '{"first": true}'


# --------------------------------------------------------------------------- #
# repair_json
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("broken", "expected"),
    [
        ('{"a": True, "b": False}', {"a": True, "b": False}),
        ('{"a": None}', {"a": None}),
        ('[1, 2, 3,]', [1, 2, 3]),
        ('{"a": 1,}', {"a": 1}),
        ('{"a": 1, "b": [1, 2,],}', {"a": 1, "b": [1, 2]}),
        ('{"a": 1} // trailing note', {"a": 1}),
        ('{"url": "https://example.com//path"}', {"url": "https://example.com//path"}),
        ('{"note": "keep // this"}', {"note": "keep // this"}),
    ],
)
def test_common_model_mistakes_are_repaired(broken: str, expected):
    assert json.loads(repair_json(broken)) == expected


def test_smart_quotes_become_ascii_quotes():
    repaired = repair_json('{"note": "don’t do this"}')
    assert json.loads(repaired) == {"note": "don't do this"}


def test_repair_does_not_touch_words_that_merely_contain_a_literal_name():
    """`True-ish` is a value, not a Python boolean."""
    assert json.loads(repair_json('{"a": "Nonetheless"}')) == {"a": "Nonetheless"}
    assert json.loads(repair_json('{"a": "Truthy"}')) == {"a": "Truthy"}


def test_repairing_does_not_rescue_something_that_is_not_json():
    with pytest.raises(ValueError):
        json.loads(repair_json("this is a sentence, not JSON"))


# --------------------------------------------------------------------------- #
# extract_json
# --------------------------------------------------------------------------- #


def test_plain_json_parses():
    assert extract_json('{"severity": "HIGH", "verdict": "real"}') == {
        "severity": "HIGH",
        "verdict": "real",
    }


def test_json_wrapped_in_fences_parses():
    assert extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_json_with_prose_around_it_parses():
    response = (
        "I looked at the file and here is my assessment:\n\n"
        '{"file_path": "app.py", "verdict": "vulnerable"}\n\n'
        "Let me know if you want more detail."
    )
    assert extract_json(response) == {"file_path": "app.py", "verdict": "vulnerable"}


def test_a_trailing_sentence_after_the_object_is_dropped():
    assert extract_json('{"a": 1}\nThat is the whole assessment.') == {"a": 1}


def test_a_top_level_array_parses():
    assert extract_json('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]


def test_prose_followed_by_an_array_parses():
    assert extract_json('Here are the findings: [{"a": 1}]') == [{"a": 1}]


def test_braces_inside_string_values_do_not_end_the_object_early():
    response = '{"diff": "--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b", "path": "x.py"}'
    assert extract_json(response) == {"diff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b", "path": "x.py"}


def test_escaped_quotes_inside_a_value_do_not_end_the_string():
    assert extract_json(r'{"code": "print(\"hi\")", "ok": true}') == {"code": 'print("hi")', "ok": True}


def test_nested_objects_and_arrays_are_balanced_correctly():
    payload = {"findings": [{"id": "a", "evidence": {"line": 3}}], "count": 1}
    assert extract_json(f"Result: {json.dumps(payload)} done") == payload


def test_repair_is_applied_when_the_first_parse_fails():
    assert extract_json('```json\n{"a": True, "b": [1,2,],}\n```') == {"a": True, "b": [1, 2]}


def test_smart_quotes_used_as_delimiters_are_repaired_end_to_end():
    assert extract_json("{“note”: “it’s fine”}") == {"note": "it's fine"}


def test_a_smart_quote_inside_a_value_is_left_alone():
    """It is a real character in the text the model wrote, not a syntax error."""
    assert extract_json('{"note": "it’s fine"}') == {"note": "it’s fine"}


@pytest.mark.parametrize("response", ["", "   ", "\n\n", "no json here at all", "{unclosed"])
def test_a_response_with_no_recoverable_json_raises(response: str):
    """Callers decide whether to retry or degrade; silence is never acceptable."""
    with pytest.raises(ValueError):
        extract_json(response)


def test_the_error_message_says_what_went_wrong():
    with pytest.raises(ValueError, match="empty"):
        extract_json("")


# --------------------------------------------------------------------------- #
# prompts and configuration
# --------------------------------------------------------------------------- #


def test_json_instruction_adds_a_system_turn_without_mutating_the_caller_list():
    messages = [{"role": "user", "content": "go"}]
    updated = with_json_instruction(messages)

    assert messages == [{"role": "user", "content": "go"}]
    assert updated[0]["role"] == "system"
    assert "json" in updated[0]["content"].lower()
    assert updated[1] == {"role": "user", "content": "go"}


def test_json_instruction_folds_into_an_existing_system_turn():
    updated = with_json_instruction([{"role": "system", "content": "You triage code."}])
    assert len(updated) == 1
    assert updated[0]["content"].startswith("You triage code.")
    assert "json" in updated[0]["content"].lower()


def test_defaults_are_nebius_and_nvidia_nemotron():
    client = LLMClient()
    assert DEFAULT_BASE_URL.startswith("https://")
    assert "nebius" in DEFAULT_BASE_URL
    assert DEFAULT_HEAVY_MODEL.startswith("nvidia/")
    assert DEFAULT_FAST_MODEL.startswith("nvidia/")
    assert DEFAULT_HEAVY_MODEL != DEFAULT_FAST_MODEL


def test_heavy_and_fast_map_to_different_models():
    client = LLMClient()
    assert client.model_for(ROLE_HEAVY) == DEFAULT_HEAVY_MODEL
    assert client.model_for(ROLE_FAST) == DEFAULT_FAST_MODEL
    assert client.model_for("anything-else") == DEFAULT_FAST_MODEL


def test_models_and_base_url_are_overridable_by_environment(monkeypatch):
    monkeypatch.setenv("NEBIUS_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("NEBIUS_MODEL_HEAVY", "nvidia/some-heavy")
    monkeypatch.setenv("NEBIUS_MODEL_FAST", "nvidia/some-fast")
    monkeypatch.setenv("NEBIUS_MAX_RETRIES", "5")

    client = LLMClient()
    assert client.base_url == "https://example.invalid/v1"
    assert client.heavy_model == "nvidia/some-heavy"
    assert client.fast_model == "nvidia/some-fast"
    assert client.max_retries == 5


def test_out_of_range_configuration_is_clamped_rather_than_trusted(monkeypatch):
    monkeypatch.setenv("NEBIUS_MAX_RETRIES", "-4")
    monkeypatch.setenv("NEBIUS_MAX_CONCURRENCY", "0")
    monkeypatch.setenv("NEBIUS_MAX_OUTPUT_TOKENS", "1")
    monkeypatch.setenv("NEBIUS_JSON_REPAIR_ATTEMPTS", "0")

    client = LLMClient()
    assert client.max_retries == 0
    assert client.max_concurrency == 1
    assert client.max_output_tokens >= 256
    assert client.json_repair_attempts >= 1


def _kwargs(client: LLMClient, max_output_tokens=None) -> dict:
    return client._request_kwargs(ROLE_HEAVY, None, [{"role": "user", "content": "x"}], None, max_output_tokens, None, None, None, None)


def test_the_default_budget_leaves_room_for_reasoning_before_the_answer():
    """A Nemotron answer arrives after its reasoning, out of the same max_tokens.

    Measured on Nemotron-3-Ultra-550b-a55b with the fix-advisor prompt: 1772
    reasoning tokens then 407 tokens of JSON. A budget sized for the answer
    returns finish_reason="length" and no JSON at all.
    """
    client = LLMClient()
    assert client.max_output_tokens >= 4000


def test_a_call_site_cannot_budget_less_than_the_configured_default():
    """Lowering the cap below the default does not tighten the answer, it truncates it."""
    client = LLMClient()
    assert _kwargs(client, max_output_tokens=300)["max_tokens"] == client.max_output_tokens
    assert _kwargs(client, max_output_tokens=2000)["max_tokens"] == client.max_output_tokens


def test_a_call_site_may_raise_the_budget_above_the_default():
    client = LLMClient()
    assert _kwargs(client, max_output_tokens=client.max_output_tokens + 5000)["max_tokens"] > client.max_output_tokens


def test_no_call_site_budgets_below_the_default():
    """Every reasoning-heavy call site in the package, checked in one place."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "core"
    offenders = []
    for path in sorted(root.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            match = re.search(r"max_output_tokens=(\d+)", line)
            if match and int(match.group(1)) < 4000:
                offenders.append(f"{path.name}:{lineno} -> {match.group(1)}")
    assert offenders == [], offenders


def test_describe_never_exposes_the_key(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-secret-value-1234")
    described = LLMClient().describe()

    assert described["configured"] is True
    assert "nebius-secret-value-1234" not in json.dumps(described)
    assert set(described) == {
        "configured",
        "base_url",
        "models",
        "max_output_tokens",
        "max_concurrency",
        "max_retries",
        "request_timeout",
    }


def test_an_absent_key_reports_itself_as_unconfigured():
    assert LLMClient().configured is False


def test_the_key_is_redacted_from_anything_that_is_returned_or_logged(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-secret-value-1234")
    client = LLMClient()

    message = "401 from nebius-secret-value-1234 (Authorization: Bearer nebius-secret-value-1234)"
    redacted = client._redact(message)

    assert "nebius-secret-value-1234" not in redacted
    assert "***redacted***" in redacted


def test_redaction_survives_an_empty_message_and_an_empty_key(monkeypatch):
    assert LLMClient()._redact("") == ""
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    assert LLMClient()._redact("") == ""


def test_redaction_cannot_be_defeated_by_url_encoding_the_key(monkeypatch):
    """A key echoed inside a query string is the most likely place to leak it."""
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-secret-value-1234")
    from urllib.parse import quote

    redacted = LLMClient()._redact(f"failed: /v1/chat?key={quote('nebius-secret-value-1234')}")
    assert "nebius-secret-value-1234" not in redacted


class _Choice:
    """A streamed choice carries a delta, unlike a completed one which carries a message."""

    def __init__(self, content=None):
        self.delta = type("Delta", (), {"content": content})()


class _Chunk:
    """A ChatCompletionChunk. The SDK's .stream() helper yields ChunkEvent, which has no .choices."""

    def __init__(self, content=None):
        self.choices = [_Choice(content)] if content is not None else []


class _FakeStream:
    """Stands in for the SDK's AsyncStream: yields chunks and must be closed."""

    def __init__(self, chunks, fail=False):
        self._chunks = list(chunks)
        self.closed = False
        self._fail = fail

    def __aiter__(self):
        async def generate():
            for chunk in self._chunks:
                yield chunk
            if self._fail:
                raise RuntimeError("connection reset by peer")

        return generate()

    async def close(self):
        self.closed = True


class _FakeCompletions:
    def __init__(self, stream):
        self._stream = stream
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self._stream


def _streaming_client(stream) -> tuple[LLMClient, _FakeCompletions]:
    completions = _FakeCompletions(stream)
    client = LLMClient()
    client._async_client = type("FakeAsync", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return client, completions


def _drain(agen) -> list[str]:
    async def collect():
        return [piece async for piece in agen]

    return asyncio.run(collect())


def test_streaming_concatenates_deltas_and_closes_the_stream():
    client, completions = _streaming_client(_FakeStream([_Chunk("Hello"), _Chunk(None), _Chunk(" world")]))

    pieces = _drain(client.chat_stream([{"role": "user", "content": "hi"}]))

    assert "".join(pieces) == "Hello world"
    assert completions.kwargs["stream"] is True


def test_streaming_never_goes_below_the_configured_token_budget():
    client, completions = _streaming_client(_FakeStream([_Chunk("hi")]))

    _drain(client.chat_stream([{"role": "user", "content": "hi"}], max_output_tokens=100))

    assert completions.kwargs["max_tokens"] == client.max_output_tokens


def test_a_chunk_without_choices_is_skipped_not_crashed():
    """Role-only deltas and keepalive chunks arrive with an empty choices list."""
    empty = type("Empty", (), {"choices": []})()
    client, _ = _streaming_client(_FakeStream([empty, _Chunk("ok")]))

    assert _drain(client.chat_stream([{"role": "user", "content": "hi"}])) == ["ok"]


def test_a_stream_that_raises_surfaces_as_an_llm_error():
    from app.core.llm import LLMError

    client, _ = _streaming_client(_FakeStream([], fail=True))

    with pytest.raises(LLMError):
        _drain(client.chat_stream([{"role": "user", "content": "hi"}]))


def test_the_stream_helper_is_not_used():
    """openai >= 3 makes .stream() yield ChunkEvent, which carries the chunk on .chunk.

    A source-level guard, because the failure only shows up on a real streamed
    request: every chunk raises AttributeError and the endpoint emits an error
    event instead of an answer. Found by asking the live API.
    """
    import inspect

    import app.core.llm as llm_module

    source = inspect.getsource(llm_module.LLMClient.chat_stream)
    assert "completions.stream(" not in source, "the .stream() helper yields ChunkEvent, not a chunk"
    assert "create(" in source and "stream=True" in source
