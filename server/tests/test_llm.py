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
