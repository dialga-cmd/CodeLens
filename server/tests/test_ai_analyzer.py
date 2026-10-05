"""Tests for the model-backed passes over measured findings.

The behaviour checked here is the triage/remediation handover: what the model
returns has to reach the findings, and a finding nobody ruled on still has to
get advice written for it. That handover was untested, and a label mismatch in
it meant a whole repository finished with no model-written remediation and no
error to show for it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.ai_analyzer import AIAnalyzer
from app.core.security import SecurityScanner


class FakeClient:
    """Stands in for the LLM client, answering each call from a canned script.

    Responses are consumed in call order, which keeps the double independent of
    the prompt text: the caller knows how many batches it is about to make.
    """

    def __init__(self, responses: list[Any], configured: bool = True) -> None:
        self.responses = list(responses)
        self.configured = configured
        self.model_for = lambda role: "fake/model"
        self.max_concurrency = 1
        self.calls: list[list[dict[str, Any]]] = []

    def chat_json_sync(self, messages, **kwargs):  # noqa: ANN001 - test double
        self.calls.append(list(messages))
        payload = self.responses.pop(0) if self.responses else {}

        class Result:
            usage_dict = staticmethod(lambda: {})
            finish_reason = "stop"

        return payload, Result()


def scanner_finding(rule: str = "hardcoded_secret", line: int = 10, path: str = "app/main.py") -> dict[str, Any]:
    """A finding exactly as the static scanner emits it, triage label included."""
    return {
        "id": f"{rule}:{path}:{line}",
        "rule": rule,
        "name": "Hardcoded credential",
        "severity": "CRITICAL",
        "description": "A credential appears in source.",
        "file_path": path,
        "line": line,
        "snippet": 'API_KEY = "abc123"',
        "recommendation": "Move the key to configuration.",
        "detector": "pattern",
        "confidence": "high",
        "source": "static-analysis",
        "triage": "pending",
    }


@pytest.fixture
def analyzer() -> AIAnalyzer:
    instance = AIAnalyzer.__new__(AIAnalyzer)
    instance.usage_log = []
    instance.role = "heavy"
    instance.model = None
    instance._progress_callback = None
    return instance


def script(analyzer: AIAnalyzer, responses: list[Any]) -> FakeClient:
    """Point the analyzer at a client that answers from a canned script."""
    client = FakeClient(responses)
    analyzer.client = client
    return client


def test_scanner_labels_findings_pending() -> None:
    """The premise the rest of this file rests on: the scanner says 'pending'."""
    source = 'API_KEY = "sk-live-abc123def456ghi789"\n'

    findings = SecurityScanner().scan_file("app/main.py", source, language="python")

    assert findings, "the fixture should match a rule"
    assert all(finding["triage"] == "pending" for finding in findings), findings


def test_pending_findings_get_remediation(analyzer: AIAnalyzer) -> None:
    """A finding the scanner flagged but the model never ruled on still needs advice.

    This is the regression. The filter used to accept only the labels a verdict can
    produce, and 'pending' is not one of them, so a repository where the model
    confirmed nothing got no remediation at all - reported as "0 of N findings"
    without ever calling the model.
    """
    findings = [scanner_finding(line=10), scanner_finding(line=20), scanner_finding(line=30)]

    # One batch, because 3 findings fit in a single FIX_CHUNK_SIZE chunk.
    client = script(analyzer, [{"fixes": [{"id": f["id"], "recommendation": "Move it to config."} for f in findings]}])

    fixes, _ = analyzer.recommend_fixes(findings)

    assert len(fixes) == 3, "an unreviewed candidate is exactly the one that needs advice"
    assert all(f["recommendation"] == "Move it to config." for f in fixes.values())
    assert len(client.calls) == 1


def test_dismissed_findings_are_not_advised_on(analyzer: AIAnalyzer) -> None:
    """Advice on a dismissed finding would contradict the dismissal shown beside it."""
    dismissed = scanner_finding()
    dismissed["triage"] = "dismissed"
    pending = scanner_finding(line=20)

    script(analyzer, [{"fixes": [{"id": pending["id"], "recommendation": "Move it to config."}]}])

    fixes, _ = analyzer.recommend_fixes([dismissed, pending])

    assert list(fixes) == [pending["id"]]


def test_verdicts_are_applied_and_counted(analyzer: AIAnalyzer) -> None:
    findings = [scanner_finding(line=10), scanner_finding(line=20)]
    script(
        analyzer,
        [
            {
                "verdicts": [
                    {"id": findings[0]["id"], "verdict": "confirm", "severity": "HIGH", "explanation": "live key"},
                    {"id": findings[1]["id"], "verdict": "dismiss", "explanation": "test fixture"},
                ]
            }
        ],
    )

    triaged, _, _ = analyzer.triage_findings(findings)

    assert triaged[0]["triage"] == "confirmed"
    assert triaged[0]["triage_note"] == "live key"
    assert triaged[1]["triage"] == "dismissed"


def test_unusable_verdicts_are_reported(analyzer: AIAnalyzer) -> None:
    """A verdict that never arrives has to be visible, or '0 confirmed' reads as a judgement."""
    findings = [scanner_finding(line=10), scanner_finding(line=20), scanner_finding(line=30)]
    reported: list[str] = []
    analyzer._progress_callback = reported.append
    script(
        analyzer,
        [
            {
                "verdicts": [
                    {"id": findings[0]["id"], "verdict": "dismiss", "explanation": "safe"},
                    # The model skipped this id entirely.
                    # And this one uses a label this code does not know.
                    {"id": findings[2]["id"], "verdict": "probably-fine", "explanation": "looks ok"},
                ]
            }
        ],
    )

    triaged, _, _ = analyzer.triage_findings(findings)

    assert [f.get("triage") for f in triaged] == ["dismissed", "pending", "pending"]
    assert any("no usable verdict" in line for line in reported), reported


def test_apply_verdict_rejects_unknown_labels() -> None:
    instance = AIAnalyzer.__new__(AIAnalyzer)
    finding = scanner_finding()

    assert instance._apply_verdict(finding, {"verdict": "confirm"}) is True
    assert instance._apply_verdict(finding, {"verdict": "maybe"}) is False