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

def _snapshot_with_analysis() -> dict:
    return {
        "repo_url": "https://github.com/pallets/flask",
        "head_sha": "d73fa1cdcbd8b1465c151db8924ba58b1dd14e35",
        "stats": {"total_files": 96, "total_loc": 18352, "hotspot_count": 8, "dismissed_findings": 71},
        "hotspots": [{"file_path": "src/flask/app.py", "score": 0.751, "complexity": 129, "commits": 11, "findings": 6}],
        "vulnerabilities": [
            {"file_path": "src/flask/sessions.py", "line": 277, "severity": "HIGH", "rule": "weak_hash", "triage": "confirmed"},
            {"file_path": "tests/test_basic.py", "line": 12, "severity": "LOW", "rule": "debug_mode", "triage": "dismissed"},
        ],
    }


def test_the_digest_carries_the_measurements_a_file_scoped_question_needs():
    """Asked "which file has the worst hotspot score" over file context, the model
    answered that hotspot data did not exist. It was in the snapshot, unshown."""
    from app.core.chat_context import build_analysis_digest

    digest = build_analysis_digest(_snapshot_with_analysis())

    assert "src/flask/app.py" in digest
    assert "8 hotspots" in digest
    assert "71 dismissed" in digest


def test_the_digest_lists_only_findings_that_survived_triage():
    from app.core.chat_context import build_analysis_digest

    digest = build_analysis_digest(_snapshot_with_analysis())

    assert "src/flask/sessions.py:277" in digest
    assert "tests/test_basic.py" not in digest


def test_file_scoped_context_includes_the_digest():
    from app.core.chat_context import build_file_context

    snapshot = _snapshot_with_analysis()
    snapshot["repo_path"] = ""
    snapshot["files"] = [{"file_path": "src/flask/app.py", "language": "python", "complexity": 129, "loc": 1628}]

    context, _ = build_file_context(snapshot, [{"file_path": "src/flask/app.py"}], "explain the hotspot")

    assert "Top ranked hotspots" in context
    assert "src/flask/app.py" in context


def test_the_digest_stays_small_enough_to_prefix_every_prompt():
    from app.core.chat_context import build_analysis_digest

    snapshot = _snapshot_with_analysis()
    snapshot["hotspots"] = [{"file_path": f"f{i}.py", "score": 0.1} for i in range(50)]
    snapshot["vulnerabilities"] = [{"file_path": f"g{i}.py", "line": i, "severity": "LOW", "triage": "confirmed"} for i in range(90)]

    assert len(build_analysis_digest(snapshot)) < 3000


def test_architecture_written_under_its_own_key_is_still_read():
    """A live run answered {"repo_identity": ..., "architecture": {...}}.

    Reading only the documented keys produced an empty panel that still named the
    model as its author, which reads as "the analysis found nothing".
    """
    from app.core.ai_analyzer import AIAnalyzer

    cleaned = AIAnalyzer._clean_architecture(
        {
            "repo_identity": {"name": "flask"},
            "architecture": {
                "summary": "A WSGI microframework.",
                "pattern": "microframework",
                "boundaries": ["wsgi-application", "jinja2-templating"],
                "entrypoints": [{"name": "flask-cli", "location": "src/flask/cli.py", "description": "Click CLI"}],
            },
        },
        "nvidia/test",
    )

    assert cleaned["summary"] == "A WSGI microframework."
    assert cleaned["pattern"] == "microframework"
    assert [layer["name"] for layer in cleaned["layers"]] == ["wsgi-application", "jinja2-templating"]
    assert cleaned["entry_points"][0]["file_path"] == "src/flask/cli.py"
    assert cleaned["extracted"] is True


def test_the_documented_architecture_shape_is_unchanged():
    from app.core.ai_analyzer import AIAnalyzer

    cleaned = AIAnalyzer._clean_architecture(
        {
            "summary": "s",
            "pattern": "p",
            "layers": [{"name": "L", "files": ["a.py"], "responsibility": "r"}],
            "entry_points": [{"file_path": "b.py", "role": "x", "calls": ["c.py"]}],
            "data_flow": ["one", "two"],
            "extension_points": [{"file_path": "d.py", "how": "add a route"}],
            "risks": [{"file_path": "e.py", "risk": "cycles"}],
        },
        "nvidia/test",
    )

    assert cleaned["layers"] == [{"name": "L", "files": ["a.py"], "responsibility": "r"}]
    assert cleaned["entry_points"][0]["calls"] == ["c.py"]
    assert cleaned["data_flow"] == ["one", "two"]


def test_an_unusable_architecture_answer_is_marked_as_such():
    """Empty with generated_by set reads as a finding; it has to read as a failure."""
    from app.core.ai_analyzer import AIAnalyzer

    cleaned = AIAnalyzer._clean_architecture({"unrelated": True}, "nvidia/test")

    assert cleaned["extracted"] is False
    assert cleaned["generated_by"] == "nvidia/test"
    assert cleaned["summary"] == ""
