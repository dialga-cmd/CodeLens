"""The deterministic pre-scan, and the triage verdict shape that reaches the UI.

Two things are load-bearing here. The scanner has to *find* real patterns, and it
has to stay quiet enough to be worth reading - a rule that fires on every
file turns the report into noise, which is why comment and placeholder lines are
pinned here too. And every finding has to carry a real location, because the
model is only allowed to rule on things the scanner actually saw.
"""

from __future__ import annotations

import pytest

from app.core.security import (
    RULES,
    SECRET_ASSIGNMENT,
    SecurityScanner,
    rank_findings,
    shannon_entropy,
    summarize,
)


def rules_for(content: str, language: str = "python") -> set[str]:
    return {finding["rule"] for finding in SecurityScanner().scan_file("f.py", content, language)}


@pytest.mark.parametrize(
    ("rule", "content"),
    [
        ("aws_access_key", "key = 'AKIAIOSFODNN7EXAMPLE'"),
        ("github_token", "GITHUB_TOKEN = 'ghp_0123456789abcdefghijklmnopqrstuvwxyz'"),
        ("slack_token", "slack = 'xoxb-1234567890-abcdefghij'"),
        ("private_key_block", "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n"),
        ("unsafe_deserialization", "state = pickle.loads(blob)"),
        ("code_execution", "value = eval(user_input)"),
        ("shell_injection", "os.system('rm -rf ' + name)"),
        ("shell_injection", "subprocess.run(cmd, shell=True)"),
        ("sql_injection", "cursor.execute('SELECT * FROM users WHERE id = ' + user_id)"),
        ("weak_hash", "digest = hashlib.md5(payload).hexdigest()"),
        ("insecure_random", "token = random.random()"),
        ("insecure_transport", "requests.get(url, verify=False)"),
        ("debug_mode", "DEBUG = True"),
        ("xss_innerhtml", "node.innerHTML = userValue"),
        ("jwt_none_algorithm", "payload = jwt.decode(token, key, verify=False)"),
        ("cors_wildcard", "allow_origins=['*']"),
        ("path_traversal", "path = os.path.join(root, request.args['name'])"),
        ("open_redirect", "return redirect(request.args['next'])"),
    ],
)
def test_every_rule_fires_on_its_own_pattern(rule: str, content: str):
    assert rule in rules_for(content), f"{rule} did not match its own example"


def test_hardcoded_secret_is_found():
    findings = SecurityScanner().scan_file(
        "config.py", 'API_KEY = "8f3b91aa77c2e4109dbe6f0c11223344"\n', "python"
    )
    assert "hardcoded_secret" in {finding["rule"] for finding in findings}


def test_documented_placeholder_is_not_reported_as_a_secret():
    """The rule that keeps the report readable: docs are full of fake keys."""
    documentation = (
        'API_KEY = "your-api-key-here"\n'
        'PASSWORD = "changeme"\n'
        "# token = 'aaaaaaaaaaaa'\n"
        'EXAMPLE_SECRET = "xxxxxxxxxxxxx"\n'
        'apiKey = "placeholder"\n'
    )
    assert "hardcoded_secret" not in rules_for(documentation)


def test_high_entropy_value_is_reported_with_its_entropy():
    findings = SecurityScanner().scan_file(
        "settings.py",
        'api_token = "uZ3rK9vXq1LmT7bN4wS6yA2cD5fG8hJ0kL"\n',
        "python",
    )
    entropy = [finding for finding in findings if finding["rule"] == "high_entropy_value"]
    assert entropy, "a random-looking token should be flagged"
    assert entropy[0]["detector"] == "entropy"
    assert entropy[0]["entropy"] > 4.2
    assert entropy[0]["confidence"] in {"low", "medium", "high"}


def test_repetitive_value_is_not_treated_as_a_leaked_key():
    """High length is not evidence; ``aaaaaaaaaaaaaaaaaaaa`` has no entropy."""
    findings = SecurityScanner().scan_file(
        "settings.py", 'token = "' + "ab" * 20 + '"\n', "python"
    )
    assert "high_entropy_value" not in {finding["rule"] for finding in findings}


def test_shannon_entropy_orders_strings_sensibly():
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaaaaaa") == 0.0
    assert shannon_entropy("abcdefgh") > shannon_entropy("aabbccdd")


@pytest.mark.parametrize(
    ("value", "looks_random"),
    [
        ("9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c", True),
        ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", False),
        ("the quick brown fox jumps over it", False),
    ],
)
def test_looks_random_separates_keys_from_prose(value: str, looks_random: bool):
    assert SecurityScanner._looks_random(value, shannon_entropy(value)) is looks_random


def test_the_entropy_detector_actually_runs():
    """A dead detector looks exactly like a clean repository.

    The assignment pattern used to be written across several lines, so the
    trailing whitespace in the source became part of the regex and no single
    line could ever match. This pins that it fires.
    """
    assert SECRET_ASSIGNMENT.search('api_token = "uZ3rK9vXq1LmT7bN4wS6yA2cD5fG8hJ0kL"')
    assert SECRET_ASSIGNMENT.search("password: 'correct-horse-battery-staple'")
    assert not SECRET_ASSIGNMENT.search('endpoint = "https://example.com/v1"')
    assert not SECRET_ASSIGNMENT.search("timeout = 30")


def test_findings_carry_the_evidence_the_model_needs():
    content = "import subprocess\n\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n"
    findings = SecurityScanner().scan_file("pkg/run.py", content, "python")
    shell = next(finding for finding in findings if finding["rule"] == "shell_injection")

    assert shell["file_path"] == "pkg/run.py"
    assert shell["line"] == 5
    assert "subprocess.run" in shell["snippet"]
    assert shell["source"] == "static-analysis"
    assert shell["triage"] == "pending"
    assert shell["id"] == f"shell_injection:pkg/run.py:5"
    assert shell["recommendation"]


def test_line_numbers_point_at_real_lines():
    content = "\n".join(f"line_{n} = {n}" for n in range(1, 40)) + "\nresult = eval(data)\n"
    findings = SecurityScanner().scan_file("f.py", content, "python")
    evaluation = next(finding for finding in findings if finding["rule"] == "code_execution")
    assert content.splitlines()[evaluation["line"] - 1] == "result = eval(data)"


def test_duplicate_matches_on_one_line_are_reported_once():
    content = "a = eval(x); b = eval(y)\n"
    findings = [
        finding
        for finding in SecurityScanner().scan_findings("f.py", content, "python")
        if finding.id == "code_execution"
    ]
    assert len(findings) == 1


def test_scanning_is_bounded_per_rule_so_one_file_cannot_flood_the_report():
    content = "".join(f"result_{n} = eval(data_{n})\n" for n in range(200))
    findings = [
        finding
        for finding in SecurityScanner().scan_findings("f.py", content, "python")
        if finding.id == "code_execution"
    ]
    assert len(findings) == 5


def test_rule_ids_are_unique_and_stable():
    """Ids end up in finding keys, caches and the model's verdicts."""
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    assert all(rule.id == rule.id.lower() for rule in RULES)


def test_every_rule_documented_as_claimed():
    for rule in RULES:
        assert rule.name and rule.description and rule.recommendation
        assert rule.severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}


def test_scan_files_skips_unreadable_records():
    findings = SecurityScanner().scan_files(
        [
            {"file_path": "a.py", "content": "eval(x)", "language": "python"},
            {"file_path": "b.py", "content": "", "language": "python"},
            {"file_path": "", "content": "eval(y)"},
        ]
    )
    assert [finding["file_path"] for finding in findings] == ["a.py", ""]


def test_empty_content_is_never_scanned():
    assert SecurityScanner().scan_findings("a.py", "") == []


def test_rank_puts_the_worst_first_and_is_stable():
    findings = [
        {"severity": "LOW", "file_path": "a.py", "line": 2},
        {"severity": "CRITICAL", "file_path": "z.py", "line": 9},
        {"severity": "LOW", "file_path": "a.py", "line": 1},
        {"severity": "HIGH", "file_path": "m.py", "line": 4},
        {"severity": "NONSENSE", "file_path": "b.py", "line": 1},
    ]
    assert [finding["severity"] for finding in rank_findings(findings)] == [
        "CRITICAL",
        "HIGH",
        "LOW",
        "LOW",
        "NONSENSE",
    ]
    assert rank_findings(findings)[:3][2]["line"] == 1


def test_summary_counts_severity_files_and_total():
    findings = [
        {"severity": "HIGH", "file_path": "a.py", "line": 1},
        {"severity": "HIGH", "file_path": "b.py", "line": 1},
        {"severity": "low", "file_path": "a.py", "line": 2},
    ]
    assert summarize(findings) == {
        "total": 3,
        "by_severity": {"HIGH": 2, "LOW": 1},
        "dismissed": 0,
        "files_affected": 2,
    }


def test_a_dismissed_finding_is_not_counted_as_a_vulnerability():
    """zod: the model dismissed 37 of 37, two of them HIGH.

    Reporting "2 high vulnerabilities" for a repository the model judged to have
    none is the first number a reviewer reads, so dismissals are counted apart.
    """
    findings = [
        {"severity": "HIGH", "file_path": "a.ts", "line": 1, "triage": "dismissed"},
        {"severity": "HIGH", "file_path": "b.ts", "line": 1, "triage": "dismissed"},
        {"severity": "MEDIUM", "file_path": "c.ts", "line": 1, "triage": "confirmed"},
    ]
    summary = summarize(findings)

    assert summary["by_severity"] == {"MEDIUM": 1}
    assert summary["total"] == 1
    assert summary["dismissed"] == 2
    assert summary["files_affected"] == 1


def test_a_finding_with_no_triage_verdict_is_still_counted():
    findings = [{"severity": "HIGH", "file_path": "a.py", "line": 1, "triage": "pending"}]

    assert summarize(findings)["by_severity"] == {"HIGH": 1}
