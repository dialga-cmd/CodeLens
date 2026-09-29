"""Answering questions about one repository, with tools.

The assistant is given the analysis up front - stack, hotspots, findings,
dependency inventory, graph metrics - and three tools to fill the gaps. It is not
given the source tree, so when it needs to see a file it has to ask for it, and
every file it reads is recorded and shown to the user as a source.

Streaming and non-streaming answers both use the tools, but not the same way:

* the plain endpoint runs one bounded tool loop, so the model can ask, read, and
  then answer in the same turn;
* the streaming endpoint gathers evidence first in a short, cheap planning round
  and then streams the answer, because a stream cannot be resumed after a tool
  round-trip.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Sequence

from .chat_context import (
    build_file_context,
    build_general_context,
    select_context_files,
)
from .code_tools import TOOL_SCHEMAS, CodeTools
from .llm import ROLE_FAST, LLMError, get_llm_client
from .prompts import load_prompt

CHAT_ROLE = ROLE_FAST
MAX_TOOL_ROUNDS = 4
MAX_PLANNING_ROUNDS = 2

SYSTEM_PROMPT = (
    "You are CodeLens, an assistant that answers questions about one specific codebase. "
    "Answer only from the evidence you have: the analysis summary, and any file you read with a tool. "
    "Cite file paths and line numbers. When the evidence does not answer the question, say that plainly "
    "and name what you would need to look at. Do not invent files, functions or behaviour."
)


class ChatService:
    """One question about one stored analysis."""

    def __init__(self, snapshot: dict[str, Any], client: Any | None = None) -> None:
        self.snapshot = snapshot
        self.client = client or get_llm_client()
        self.tools = CodeTools(snapshot)

    @property
    def model_id(self) -> str:
        return self.client.model_for(CHAT_ROLE)

    # -- context ----------------------------------------------------------- #

    def initial_context(self, query: str) -> tuple[str, list[str]]:
        """The analysis slice most likely to answer ``query``, and the files used."""
        matched = select_context_files(self.snapshot, query)
        if matched:
            return build_file_context(self.snapshot, matched, query)
        return build_general_context(self.snapshot), []

    def _messages(self, query: str, context: str, used_files: Sequence[str], extra_evidence: str = "") -> list[dict[str, str]]:
        template = load_prompt("chat_prompt.txt")
        sections = [f"{template}\n\nAnalysis of {self.snapshot.get('repo_url', '')}:"]
        if context:
            sections.append(context)
        if used_files:
            sections.append("Files already in evidence: " + ", ".join(used_files))
        if extra_evidence:
            sections.append(f"\nEvidence gathered with tools:\n{extra_evidence}")
        sections.append(
            f"\nQuestion: {query}\n\nAnswer from the evidence above, citing file paths."
            " Use a tool if you still need a file you have not seen."
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(sections)},
        ]

    # -- non-streaming ----------------------------------------------------- #

    async def answer(self, query: str) -> dict[str, Any]:
        """Answer with a full tool loop: ask, read, then respond."""
        context, used_files = self.initial_context(query)
        messages = self._messages(query, context, used_files)

        try:
            result, tool_calls = await self.client.chat_with_tools(
                messages,
                TOOL_SCHEMAS,
                self.tools.execute,
                role=CHAT_ROLE,
                temperature=0.2,
                max_rounds=MAX_TOOL_ROUNDS,
            )
        except LLMError as error:
            return {"error": str(error), "answer": ""}

        return {
            "answer": result.text,
            "model": result.model,
            "usage": result.usage_dict(),
            "sources": self.sources(used_files),
            "tools_used": [record.to_dict() for record in tool_calls],
        }

    # -- streaming --------------------------------------------------------- #

    async def gather_evidence(self, query: str) -> tuple[str, str, list[str], list[dict[str, Any]]]:
        """Run short tool rounds before streaming, and return what they found.

        Returns ``(context, evidence_text, files_used, tool_calls)``. If the model
        does not call a tool, or the provider is unavailable, the evidence is
        empty and the answer is streamed from the analysis summary alone.
        """
        context, used_files = self.initial_context(query)
        messages = self._messages(query, context, used_files)
        messages[1]["content"] += (
            "\nBefore answering, call a tool if you need a file you have not seen. "
            "Do not answer from memory about code you have not read."
        )

        try:
            _, tool_calls = await self.client.chat_with_tools(
                messages,
                TOOL_SCHEMAS,
                self.tools.execute,
                role=CHAT_ROLE,
                temperature=0.0,
                max_output_tokens=300,
                max_rounds=MAX_PLANNING_ROUNDS,
            )
        except LLMError as error:
            print(f"Tool planning round failed: {error}", flush=True)
            return context, "", used_files, []

        if not tool_calls:
            return context, "", used_files, []

        # The planning round produced tool output, not an answer. Replay the
        # findings as plain context and let the streamed turn write the answer.
        evidence = "\n".join(
            f"Tool {record.name}({json.dumps(record.arguments, default=str)[:200]}) returned:\n{record.result_preview}"
            for record in tool_calls
        )
        return context, evidence[:6000], used_files, [record.to_dict() for record in tool_calls]

    async def stream(self, query: str) -> AsyncIterator[dict[str, str]]:
        """Yield answer events, having gathered tool evidence first.

        Events are shaped for Server-Sent Events and forwarded unchanged by the
        HTTP layer: one ``sources`` event naming what the answer is based on,
        then ``delta`` events, then a final ``done`` (or ``error``).
        """
        context, evidence, used_files, tool_calls = await self.gather_evidence(query)
        yield {
            "event": "sources",
            "data": json.dumps(
                {
                    "sources": self.sources(used_files),
                    "tools_used": tool_calls,
                    "model": self.model_id,
                }
            ),
        }

        messages = self._messages(query, context, used_files, extra_evidence=evidence)
        try:
            async for delta in self.client.chat_stream(messages, role=CHAT_ROLE, temperature=0.3):
                yield {"event": "delta", "data": json.dumps({"text": delta})}
        except LLMError as error:
            yield {"event": "error", "data": json.dumps({"message": str(error)})}
            yield {"event": "done", "data": json.dumps({"ok": False})}
            return

        yield {"event": "done", "data": json.dumps({"ok": True})}

    # -- reporting --------------------------------------------------------- #

    def sources(self, used_files: Sequence[str]) -> list[dict[str, Any]]:
        """What the answer is based on: context files first, then anything read."""
        sources: list[dict[str, Any]] = [{"path": path, "source": "context"} for path in used_files]
        seen = {entry["path"] for entry in sources}

        for call in self.tools.calls:
            path = str(call["arguments"].get("path", ""))
            if not path or path in seen:
                continue
            seen.add(path)
            sources.append({"path": path, "source": "tool"})
        return sources
