"""Every grounding query key must be a rule the scanner actually emits.

A key that does not exist is dead weight. A rule with no key silently falls back
to a generic "vulnerability remediation OWASP" query, which returns guidance
about a class of bug the finding is not.

The four mismatched keys in this file's history - `insecure_eval`,
`disabled_tls_verification`, `xss`, `unsigned_jwt` - matched no rule id at all.
Four of the eighteen rules reached Tavily with the wrong question, and four
credential rules asked about the wrong provider.
"""

from __future__ import annotations

from app.core.research import GROUNDING_QUERIES
from app.core.security import RULES


def test_every_rule_has_a_grounding_query():
    emitted = {rule.id for rule in RULES} | {"high_entropy_value"}  # entropy is a rule id too
    assert emitted <= set(GROUNDING_QUERIES), sorted(emitted - set(GROUNDING_QUERIES))


def test_no_query_key_is_for_a_rule_that_does_not_exist():
    emitted = {rule.id for rule in RULES} | {"high_entropy_value"}
    assert set(GROUNDING_QUERIES) <= emitted, sorted(set(GROUNDING_QUERIES) - emitted)


def test_each_query_names_the_bug_class_it_is_asked_about():
    """The keys that were wrong asked about a different vulnerability than the one found."""
    assert "Tls" in GROUNDING_QUERIES["insecure_transport"] or "TLS" in GROUNDING_QUERIES["insecure_transport"]
    assert "scripting" in GROUNDING_QUERIES["xss_innerhtml"]
    assert "JSON web token" in GROUNDING_QUERIES["jwt_none_algorithm"]
    assert "eval" in GROUNDING_QUERIES["code_execution"]
    assert "AWS" in GROUNDING_QUERIES["aws_access_key"]
    assert "GitHub" in GROUNDING_QUERIES["github_token"]


def _finding(rule: str, triage: str = "pending") -> dict:
    return {"id": f"{rule}:app.py:1", "rule": rule, "name": "Weak hash", "triage": triage}


def _queried_ids(monkeypatch, findings, limit: int) -> list[str]:
    """Run ground_findings with the searches stubbed out, returning the ids it asked about."""
    from app.core.research import ResearchEngine

    engine = ResearchEngine(client=None)
    asked: list[str] = []

    def fake_run(self, jobs, include_domains=None):
        asked.extend(job[0] for job in jobs)
        return []

    monkeypatch.setattr(ResearchEngine, "available", property(lambda self: True))
    monkeypatch.setattr(ResearchEngine, "_run", fake_run)
    engine.ground_findings(findings, limit=limit)
    return asked


def test_a_dismissed_finding_is_never_grounded(monkeypatch):
    """Citations must not be attached to a finding the model dismissed as a false positive."""
    asked = _queried_ids(
        monkeypatch,
        [_finding("weak_hash", "dismissed"), _finding("sql_injection", "confirmed")],
        limit=5,
    )

    assert asked == ["sql_injection:app.py:1"]


def test_grounding_spends_its_budget_on_findings_that_survived(monkeypatch):
    """Triage dismisses the easy majority, so budget spent on them is budget wasted."""
    dismissed = [_finding("weak_hash", "dismissed") for _ in range(5)]
    surviving = [_finding("sql_injection", "confirmed"), _finding("path_traversal", "pending")]

    assert _queried_ids(monkeypatch, dismissed + surviving, limit=3) == [
        "sql_injection:app.py:1",
        "path_traversal:app.py:1",
    ]