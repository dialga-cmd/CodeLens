"""Chat: how an answer is built, and what it is allowed to contain.

The tests here are about two things that only fail in front of a user. The first
is context: a question can be answerable from the measurements the pipeline
already took, and if the model is not shown them it will say they do not exist.
The second is the tool loop, which has a failure mode specific to streaming and
to these models - asked for a tool call, they will sometimes write one out as
text instead of returning it as a structured call, and text is what the user
reads.
"""

from __future__ import annotations

import asyncio
import json

from app.core.chat_context import build_analysis_digest
from app.core.chat_service import ChatService, is_tool_call_text, _split_safe
from app.core.llm import ChatResult, LLMClient, LLMError


def _snapshot() -> dict:
    return {
        "repo_url": "https://github.com/pallets/flask",
        "head_sha": "d73fa1cdcbd8b1465c151db8924ba58b1dd14e35",
        "repo_path": "",
        "files": [{"file_path": "src/flask/app.py", "language": "python", "complexity": 129, "loc": 1628}],
        "stats": {"total_files": 96, "total_loc": 18352, "hotspot_count": 8, "dismissed_findings": 71},
        "hotspots": [{"file_path": "src/flask/app.py", "score": 0.751, "complexity": 129, "commits": 11, "findings": 0}],
        "vulnerabilities": [
            {"file_path": "src/flask/sessions.py", "line": 277, "severity": "HIGH", "rule": "weak_hash", "triage": "confirmed"},
            {"file_path": "tests/test_basic.py", "line": 12, "severity": "LOW", "rule": "debug_mode", "triage": "dismissed"},
        ],
        "architecture": {"summary": "A WSGI microframework.", "layers": []},
    }


def test_a_question_about_a_measurement_can_be_answered_from_the_digest():
    """The live failure: "which file has the worst hotspot score" was answered
    with "that data is not present", while eight ranked hotspots went unread."""
    digest = build_analysis_digest(_snapshot())

    assert "src/flask/app.py" in digest
    assert "8 hotspots" in digest


def test_the_digest_names_no_finding_the_model_dismissed():
    digest = build_analysis_digest(_snapshot())

    assert "src/flask/sessions.py:277" in digest
    assert "tests/test_basic.py" not in digest


def test_a_digest_stays_small_with_a_large_repository():
    snapshot = _snapshot()
    snapshot["hotspots"] = [{"file_path": f"h{i}.py", "score": 0.1} for i in range(60)]
    snapshot["vulnerabilities"] = [
        {"file_path": f"v{i}.py", "line": i, "severity": "LOW", "triage": "confirmed"} for i in range(200)
    ]

    assert len(build_analysis_digest(snapshot)) < 3000


# -- text that is really a tool call ---------------------------------------- #


def test_the_markup_live_off_the_fast_model_is_recognised():
    """Exactly what Nemotron-3_5-Lightning returned, zero-width space included."""
    markup = "<\u200btool_call>\n<function=search_code>\n<parameter=query>\nhotspot\n</parameter>\n</function>\n</\u200btool_call>"

    assert is_tool_call_text(markup) is True


def test_the_other_markup_shapes_are_recognised_too():
    for markup in ("<tool_calls>[{", "  <function=read_file>", "<parameter=path>a.py</parameter>", "recipient: search_code"):
        assert is_tool_call_text(markup) is True, markup


def test_a_real_answer_is_not_mistaken_for_a_tool_call():
    """The detector has to stay narrow or it starts eating answers."""
    answers = [
        "Here is the list of files that call app.logger.error:\n- src/flask/app.py:120",
        "The function `make_response` in src/flask/app.py builds a response.",
        "Use <function> notation in your answer.",
        "Send it to the recipient: the sender.",
        "",
    ]
    for answer in answers:
        assert is_tool_call_text(answer) is False, answer


# -- streaming past a tag that arrives in pieces ---------------------------- #


def _drain_split(pieces: list[str]) -> tuple[str, str]:
    """Replay deltas through the hold-back splitter, as the endpoint does."""
    pending = ""
    emitted = ""
    for piece in pieces:
        pending += piece
        safe, pending = _split_safe(pending)
        emitted += safe
    return emitted, pending


def test_a_tag_split_across_deltas_is_never_emitted():
    """The normal case: "<" arrives in one delta and "tool_call>" in the next."""
    emitted, held = _drain_split(["The list is:\n", "<", "\u200btool", "_call>\n<function=x>"])

    assert "<" not in emitted
    assert is_tool_call_text(held) is True


def test_an_ordinary_answer_streams_through_unchanged():
    answer = "Files that call it: src/flask/app.py:120 and src/flask/cli.py:9."

    emitted, held = _drain_split(["Files that call it: ", "src/flask/app.py:120", " and src/flask/cli.py:9."])

    assert emitted + held == answer


def test_a_type_annotation_in_the_middle_of_an_answer_still_streams():
    answer = "The type is List[str] here."

    emitted, held = _drain_split(["The type is ", "List[str]", " here."])

    assert emitted + held == answer


# -- the exhausted tool loop ------------------------------------------------ #


def test_a_loop_that_runs_out_of_rounds_is_told_to_answer_not_to_call_again():
    """Live: the model kept searching, ran out of rounds, and its written-out
    tool call was streamed to the user as the answer."""
    calls: list[dict] = []

    class _Client(LLMClient):
        def __init__(self) -> None:
            super().__init__()
            self.rounds = 0

        async def _acomplete(self, kwargs, *_args, **_kwargs):
            self.rounds += 1
            message = _ToolMessage(_call("search_code", {"query": "hotspot"}))
            return _Response(message), 0.0, 1

        async def chat(self, messages, *, role=None, model=None, temperature=0.2, max_output_tokens=None):
            self.rounds += 1
            wrap_up = messages[-1]
            assert "Answer now, in prose" in wrap_up.get("content", ""), wrap_up
            return ChatResult(text="The ranked hotspots are src/flask/app.py.", model="nvidia/test")

    client = _Client()
    result, records = asyncio.run(client.chat_with_tools([{"role": "user", "content": "q"}], [{"type": "function", "function": {"name": "search_code", "description": "", "parameters": {}}}], lambda name, args: calls.append(args) or {"match_count": 0}, max_rounds=3))

    assert client.rounds == 4, "three tool rounds plus the wrap-up"
    assert len(records) == 3
    assert "src/flask/app.py" in result.text


def test_a_wrap_up_that_is_still_a_tool_call_is_reported_not_streamed():
    class _Client(LLMClient):
        def __init__(self) -> None:
            super().__init__()

        async def _acomplete(self, kwargs, *_args, **_kwargs):
            return _Response(_ToolMessage(_call("search_code", {"query": "x"}))), 0.0, 1

        async def chat(self, messages, **_kwargs):
            return ChatResult(text="<\u200btool_call>\n<function=search_code>\n</\u200btool_call>", model="nvidia/test")

    client = _Client()
    result, _ = asyncio.run(client.chat_with_tools([{"role": "user", "content": "q"}], [{"type": "function", "function": {"name": "search_code", "description": "", "parameters": {}}}], lambda name, args: {}, max_rounds=2))

    assert is_tool_call_text(result.text) is False
    assert "all 2 look-ups" in result.text


# -- what the streaming endpoint actually emits ------------------------------ #


class _StubClient:
    """Enough of LLMClient for ChatService.stream: a model id and a text stream.

    The planning round that gathers evidence is stubbed to "no tools called",
    because what these tests are about is what the streaming turn emits.
    """

    model = "nvidia/stub"

    def __init__(self, pieces, error=None):
        self._pieces = pieces
        self._error = error

    def model_for(self, _role):
        return self.model

    async def chat_with_tools(self, *_args, **_kwargs):
        return ChatResult(text="", model=self.model), []

    async def chat_stream(self, _messages, **_kwargs):
        for piece in self._pieces:
            yield piece
        if self._error:
            raise self._error


async def _stream_events(pieces, error=None) -> list[dict]:
    service = ChatService(_snapshot(), client=_StubClient(pieces, error))
    return [event async for event in service.stream("what is this repo about?")]


def _names(events) -> list[str]:
    return [event["event"] for event in events]


def _answer(events) -> str:
    return "".join(
        json.loads(event["data"])["text"] for event in events if event["event"] == "delta"
    )


def test_a_normal_answer_reaches_the_browser_as_deltas():
    events = asyncio.run(_stream_events(["This is a ", "WSGI microframework."]))

    assert _names(events) == ["sources", "delta", "delta", "done"]
    assert _answer(events) == "This is a WSGI microframework."
    assert json.loads(events[-1]["data"])["ok"] is True


def test_the_sources_event_names_the_files_the_answer_is_based_on():
    events = asyncio.run(_stream_events(["src/flask/app.py is the entry point."]))

    sources = json.loads(events[0]["data"])
    assert sources["sources"], "the reader must be able to check what was used"
    assert all({"path", "source"} <= set(entry) for entry in sources["sources"])


def test_a_token_budget_cutoff_reaches_the_browser_as_a_reason():
    """The bug in front of the user: every answer came back empty with no reason.

    The model spent its whole budget reasoning, so the stream carried no content
    and no explanation. The endpoint has to end that turn with something the UI
    can show, not with silence.
    """
    events = asyncio.run(
        _stream_events([], error=LLMError("The model used its whole token budget before answering."))
    )

    assert _names(events) == ["sources", "error", "done"]
    assert "token budget" in json.loads(events[1]["data"])["message"]
    assert json.loads(events[-1]["data"])["ok"] is False
    assert _answer(events) == "", "nothing may be invented to fill the gap"


def test_a_stream_that_says_nothing_still_produces_an_error_event():
    """The backstop: whatever the client does, an answer turn is never silent."""
    events = asyncio.run(_stream_events([]))

    assert _names(events) == ["sources", "error", "done"]
    assert json.loads(events[-1]["data"])["ok"] is False


def test_the_usage_of_the_wrap_up_is_kept():
    """Discarding the result must not discard what it cost."""

    class _Client(LLMClient):
        async def _acomplete(self, kwargs, *_args, **_kwargs):
            return _Response(_ToolMessage(_call("search_code", {"query": "x"}))), 0.0, 1

        async def chat(self, messages, **_kwargs):
            return ChatResult(
                text="<\u200btool_call></\u200btool_call>",
                model="nvidia/test",
                prompt_tokens=100,
                completion_tokens=20,
                finish_reason="stop",
            )

    result, _ = asyncio.run(_Client().chat_with_tools([{"role": "user", "content": "q"}], [{"type": "function", "function": {"name": "search_code", "description": "", "parameters": {}}}], lambda n, a: {}, max_rounds=1))

    assert result.prompt_tokens == 100
    assert result.completion_tokens == 20
    assert result.finish_reason == "stop"


# -- minimal stand-ins for the SDK response objects ------------------------- #


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, name: str, arguments: dict) -> None:
        self.id = f"call_{name}"
        self.function = _Fn(name, json.dumps(arguments))


class _ToolMessage:
    def __init__(self, call: _Call) -> None:
        self.content = None
        self.tool_calls = [call]


class _Choice:
    def __init__(self, message) -> None:
        self.message = message


class _Response:
    def __init__(self, message) -> None:
        self.choices = [_Choice(message)]


def _call(name: str, arguments: dict) -> _Call:
    return _Call(name, arguments)

# -- the bare call form ----------------------------------------------------- #


def test_a_bare_call_expression_for_an_offered_tool_is_recognised():
    """The live leak was exactly this: the model echoed the evidence label,
    'search_code({"query": "from werkzeug", "max_results": 100})', and that was
    the whole answer."""
    from app.core.chat_service import TOOL_NAMES

    assert is_tool_call_text('search_code({"query": "from werkzeug"})', TOOL_NAMES) is True
    assert is_tool_call_text('read_file(path="src/flask/app.py")', TOOL_NAMES) is True


def test_prose_that_names_a_tool_is_still_an_answer():
    from app.core.chat_service import TOOL_NAMES

    assert is_tool_call_text("The results from search_code show one hit.", TOOL_NAMES) is False
    assert is_tool_call_text("App.route() is a decorator.", TOOL_NAMES) is False


def test_a_bare_call_is_only_recognised_for_the_tools_that_were_offered():
    """Without the offered names there is nothing to match against, and guessing
    would start eating answers that mention any function-like word."""

    assert is_tool_call_text('search_code({"query": "x"})') is False


def test_the_offered_tool_names_are_the_real_ones():
    from app.core.chat_service import TOOL_NAMES

    assert set(TOOL_NAMES) == {"read_file", "search_code", "list_dependencies_of_file"}


def test_the_evidence_label_does_not_read_like_a_call():
    """The model repeated the old 'Tool search_code({...}) returned:' label back as
    its answer, so the label now names the tool in a sentence."""
    from app.core.chat_service import TOOL_NAMES
    from app.core.llm import ToolCallRecord

    record = ToolCallRecord(name="search_code", arguments={"query": "from werkzeug"}, result_preview="3 matches")
    rendered = (
        f"Results from the {record.name} tool ("
        + ", ".join(f"{key}={value!r}" for key, value in list(record.arguments.items())[:4])
        + f"):\n{record.result_preview}"
    )

    assert rendered.startswith("Results from the search_code tool (query='from werkzeug'):")
    assert is_tool_call_text(rendered, TOOL_NAMES) is False
