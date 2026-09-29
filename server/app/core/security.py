"""Deterministic security pre-scan.

Regexes and entropy checks run over every file before any model is involved, so
the model is asked to triage a short list of concrete, located candidates instead
of hunting for problems in a wall of code. Findings carry the evidence that
produced them, which keeps the model honest: it can disagree with the scanner,
but it cannot invent a location.

This is pattern matching, not a vulnerability database. Anything it reports is a
candidate for review, and the UI says so.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Findings at or above this severity are always shown first in the UI.
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

# Lines that are examples, tests or documentation get a much higher entropy bar
# before they are called a leaked secret.
LOW_SIGNAL_CONTEXT = re.compile(
    r"(?i)\b(example|sample|placeholder|example\.com|xxx+|your[_-]?\w+|todo|fixme|mock|dummy|redacted|changeme)\b"
)

ENTROPY_THRESHOLD = 4.2
# Hex and base64 keys are long but low-entropy per character (16 or 64 symbols),
# so length plus charset has to count for something on its own.
ENTROPY_LONG_VALUE_LENGTH = 32
ENTROPY_LONG_VALUE_THRESHOLD = 3.5
ENTROPY_MIN_LENGTH = 24

HEX_LIKE = re.compile(r"^[0-9a-fA-F]+$")


def shannon_entropy(value: str) -> float:
    """Bits of entropy per character. Random-looking strings score high."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return abs(-sum((count / length) * math.log2(count / length) for count in counts.values()))


@dataclass
class Rule:
    id: str
    name: str
    severity: str
    description: str
    regex: str
    languages: tuple[str, ...] = ()
    recommendation: str = ""
    # Secret-shaped text is common in documentation, so those rules ignore lines
    # that are plainly comments.
    skip_comments: bool = False

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.regex)


@dataclass
class PreScanFinding:
    """One candidate issue, with the evidence that produced it."""

    id: str
    name: str
    severity: str
    description: str
    file_path: str
    line: int
    snippet: str
    recommendation: str = ""
    detector: str = "pattern"
    confidence: str = "medium"
    entropy: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "name": self.name,
            "severity": self.severity,
            "description": self.description,
            "file_path": self.file_path,
            "line": self.line,
            "snippet": self.snippet,
            "recommendation": self.recommendation,
            "detector": self.detector,
            "confidence": self.confidence,
            "source": "static-analysis",
            "triage": "pending",
        }
        if self.entropy is not None:
            payload["entropy"] = round(self.entropy, 2)
        return payload


RULES: list[Rule] = [
    Rule(
        id="hardcoded_secret",
        name="Hardcoded credential",
        severity="CRITICAL",
        description="A credential appears to be committed in source rather than supplied by configuration.",
        regex=r"""(?ix)
            \b[\w.]*
            (?:api[_-]?key|secret|token|password|passwd|passphrase|credential|private[_-]?key)
            \w*
            \s*[:=]\s*
            ["'][^"'\n]{12,}["']
        """,
        recommendation="Move the value to an environment variable or secret store and rotate the exposed credential.",
        skip_comments=True,
    ),
    Rule(
        id="aws_access_key",
        name="AWS access key id",
        severity="CRITICAL",
        description="An AWS access key id pattern is present in the file.",
        regex=r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b",
        recommendation="Revoke the key in IAM and load credentials from the environment or an instance role.",
    ),
    Rule(
        id="private_key_block",
        name="Private key material",
        severity="CRITICAL",
        description="A PEM private key block is embedded in the repository.",
        regex=r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
        recommendation="Remove the key from the repository, rotate it, and keep keys outside version control.",
    ),
    Rule(
        id="github_token",
        name="GitHub personal access token",
        severity="CRITICAL",
        description="A GitHub token prefix followed by 36 token characters was found.",
        regex=r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b",
        recommendation="Revoke the token on GitHub and store it as a repository secret instead.",
    ),
    Rule(
        id="slack_token",
        name="Slack token",
        severity="HIGH",
        description="A Slack API token was found in source.",
        regex=r"\b(xox[abposr]-[A-Za-z0-9-]{10,})\b",
        recommendation="Revoke the token in the Slack app settings and load it from configuration.",
    ),
    Rule(
        id="unsafe_deserialization",
        name="Unsafe deserialization",
        severity="HIGH",
        description="pickle, marshal or yaml.load can execute code from untrusted input.",
        regex=r"\b(pickle\.loads?|cPickle\.loads?|marshal\.loads?|yaml\.load\s*\((?![^)]*Loader\s*=)|dill\.loads?)\b",
        recommendation="Use a data-only format such as JSON, or yaml.safe_load for untrusted input.",
    ),
    Rule(
        id="code_execution",
        name="Dynamic code execution",
        severity="HIGH",
        description="eval or exec turns a string into running code.",
        regex=r"\b(eval|exec)\s*\(",
        recommendation="Replace dynamic evaluation with an explicit dispatch table or a parser.",
    ),
    Rule(
        id="shell_injection",
        name="Shell command execution",
        severity="HIGH",
        description="A shell is invoked; if any argument is user-controlled this is command injection.",
        regex=r"(os\.system\s*\(|subprocess\.(?:run|call|Popen|check_output|check_call)\s*\([^)]*shell\s*=\s*True|child_process\.exec\s*\()",
        recommendation="Pass an argument list instead of a shell string, and never interpolate untrusted input.",
    ),
    Rule(
        id="sql_injection",
        name="SQL built by string formatting",
        severity="HIGH",
        description="A SQL statement is assembled with string concatenation or interpolation.",
        regex=r"""(?ix)
            (?:SELECT|INSERT|UPDATE|DELETE)\b[^\n]{0,160}?
            (?:%\s*\(?\s*\w|["']\s*\+\s*\w|\{\s*\w+\s*\}|f["'][^\n]*\{)
        """,
        recommendation="Use parameterised queries or an ORM; never interpolate values into SQL text.",
    ),
    Rule(
        id="path_traversal",
        name="Unvalidated path join",
        severity="MEDIUM",
        description="A file path is built from a request or query value without normalisation.",
        regex=r"(?:os\.path\.join|path\.join|Path)\s*\([^)]*(?:request|params|args|query|user_input|input)\b",
        recommendation="Resolve the path and confirm it stays inside the intended directory before reading.",
    ),
    Rule(
        id="weak_hash",
        name="Weak hash for security use",
        severity="MEDIUM",
        description="MD5 or SHA1 are broken for signatures, tokens and password storage.",
        regex=r"\b(hashlib\.(?:md5|sha1)|createHash\s*\(\s*['\"](?:md5|sha1)['\"])",
        recommendation="Use SHA-256 or better; for passwords use argon2, scrypt or bcrypt.",
    ),
    Rule(
        id="insecure_random",
        name="Insecure randomness",
        severity="MEDIUM",
        description="random or Math.random cannot be used for tokens, keys or nonces.",
        regex=r"\b(random\.(?:random|randint|choice|randrange)|Math\.random)\s*\(",
        recommendation="Use secrets.token_bytes or os.urandom for anything security relevant.",
    ),
    Rule(
        id="insecure_transport",
        name="TLS verification disabled",
        severity="HIGH",
        description="Certificate verification is turned off, which removes TLS protection.",
        regex=r"(verify\s*[:=]\s*(?:False|false|0)|rejectUnauthorized\s*:\s*false|curl\s+(?:-k|--insecure)\b|InsecureSkipVerify\s*:\s*true|ssl\._create_unverified_context)",
        recommendation="Leave verification on and install the correct CA bundle instead.",
    ),
    Rule(
        id="debug_mode",
        name="Debug mode in production code",
        severity="MEDIUM",
        description="A debug flag can expose stack traces and internal state.",
        regex=r"(?i)\b(DEBUG\s*=\s*True|debug\s*=\s*true)\b",
        recommendation="Drive the flag from configuration and default it to off.",
    ),
    Rule(
        id="xss_innerhtml",
        name="Unescaped HTML injection",
        severity="MEDIUM",
        description="Assigning to innerHTML renders whatever the value contains.",
        regex=r"\.innerHTML\s*=(?!\s*['\"`]\s*;?\s*$)[^=]",
        recommendation="Use textContent, or sanitise the value with a vetted HTML sanitiser.",
    ),
    Rule(
        id="jwt_none_algorithm",
        name="JWT signature not verified",
        severity="HIGH",
        description="A JWT is decoded with verification disabled or with the 'none' algorithm.",
        regex=r"(verify\s*=\s*False|algorithms\s*=\s*\[\s*[\"']none[\"']\s*\]|jwt\.decode\([^)]*verify\s*=\s*False)",
        recommendation="Verify the signature against the issuer's public key and pin the expected algorithm.",
    ),
    Rule(
        id="open_redirect",
        name="Unvalidated redirect target",
        severity="MEDIUM",
        description="A redirect target comes directly from user input.",
        regex=r"(?i)(redirect|sendRedirect|location\.href\s*=\s*(?:document\.)?window\.(?:location|URLSearchParams))",
        recommendation="Allow only relative paths or a fixed allowlist of destinations.",
    ),
    Rule(
        id="cors_wildcard",
        name="Wildcard CORS with credentials",
        severity="MEDIUM",
        description="Allowing any origin together with credentials disables the same-origin protection.",
        regex=r"(?i)(Access-Control-Allow-Origin[^\n]{0,40}\*|allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\])",
        recommendation="List the exact origins that need access and keep credentials off for wildcards.",
    ),
]

_COMPILED = [(rule, rule.compiled()) for rule in RULES]

# Assignment patterns worth an entropy check: these are the lines where a
# leaked key actually appears.
SECRET_ASSIGNMENT = re.compile(
    r"""(?i)\b([A-Za-z0-9_]*(?:key|secret|token|password|passwd|credential|auth)[A-Za-z0-9_]*)
        \s*[:=]\s*
        ["']([^"'\n]{ENTROPY_MIN_LENGTH,})["']
    """.replace("{ENTROPY_MIN_LENGTH,}", f"{{{ENTROPY_MIN_LENGTH},}}")
)

# Values that look like paths, URLs or format strings are not secrets.
_NOT_SECRET = re.compile(r"^(?:https?://|[./\\]|[\w./-]*\{|%s|\$\{)")


class SecurityScanner:
    """Runs the deterministic rules and the entropy check over parsed files."""

    def __init__(self, rules: Sequence[Rule] | None = None) -> None:
        self.rules = list(rules) if rules is not None else RULES
        self._compiled = [(rule, rule.compiled()) for rule in self.rules]

    def scan_file(self, file_path: str, content: str, language: str = "") -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for finding in self.scan_findings(file_path, content, language):
            findings.append(finding.to_dict())
        return findings

    def scan_findings(
        self,
        file_path: str,
        content: str,
        language: str = "",
        max_findings_per_rule: int = 5,
    ) -> list[PreScanFinding]:
        """All candidates for one file, deduplicated by rule and line."""
        if not content:
            return []

        lines = content.splitlines()
        findings: list[PreScanFinding] = []

        for rule, pattern in self._compiled:
            if rule.languages and language and language not in rule.languages:
                continue
            seen_lines: set[int] = set()
            for match in pattern.finditer(content):
                line_number = content.count("\n", 0, match.start()) + 1
                if line_number in seen_lines:
                    continue
                if rule.skip_comments and (
                    _is_comment_line(lines, line_number)
                    or LOW_SIGNAL_CONTEXT.search(_line_at(lines, line_number))
                ):
                    continue
                seen_lines.add(line_number)
                findings.append(
                    PreScanFinding(
                        id=rule.id,
                        name=rule.name,
                        severity=rule.severity,
                        description=rule.description,
                        file_path=file_path,
                        line=line_number,
                        snippet=_line_at(lines, line_number),
                        recommendation=rule.recommendation,
                        detector="pattern",
                        confidence="medium",
                    )
                )
                if len(seen_lines) >= max_findings_per_rule:
                    break

        findings.extend(self._entropy_findings(file_path, lines))
        return findings

    def _entropy_findings(self, file_path: str, lines: Sequence[str]) -> list[PreScanFinding]:
        """High-entropy values assigned to credential-shaped names."""
        findings: list[PreScanFinding] = []
        for index, line in enumerate(lines, start=1):
            if len(line) > 1000 or _is_comment_line(lines, index):
                continue
            for match in SECRET_ASSIGNMENT.finditer(line):
                value = match.group(2)
                if _NOT_SECRET.match(value) or LOW_SIGNAL_CONTEXT.search(line):
                    continue
                entropy = shannon_entropy(value)
                if not _looks_random(value, entropy):
                    continue
                findings.append(
                    PreScanFinding(
                        id="high_entropy_value",
                        name="High-entropy value assigned to a credential-like name",
                        severity="HIGH",
                        description=(
                            f"The value assigned to '{match.group(1)}' has {entropy:.2f} bits of entropy per "
                            "character, which is what a randomly generated key looks like."
                        ),
                        file_path=file_path,
                        line=index,
                        snippet=line.strip()[:200],
                        recommendation="Confirm this is a placeholder; if it is a real credential, rotate it and load it from configuration.",
                        detector="entropy",
                        confidence="medium" if entropy >= 4.6 else "low",
                        entropy=entropy,
                    )
                )
        return findings

    @staticmethod
    def _looks_random(value: str, entropy: float) -> bool:
        """High entropy, or long enough and random-looking to be worth a look."""
        if entropy >= ENTROPY_THRESHOLD:
            return True
        if len(value) >= ENTROPY_LONG_VALUE_LENGTH and entropy >= ENTROPY_LONG_VALUE_THRESHOLD:
            return bool(HEX_LIKE.match(value)) or len(set(value)) >= 12
        return False

    def scan_files(self, files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Scan a sequence of ``{"file_path", "content", "language"}`` records."""
        findings: list[dict[str, Any]] = []
        for file in files:
            content = str(file.get("content", ""))
            if not content:
                continue
            findings.extend(
                self.scan_file(
                    str(file.get("file_path", "")),
                    content,
                    str(file.get("language", "")),
                )
            )
        return findings


def _line_at(lines: Sequence[str], line_number: int) -> str:
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()[:200]
    return ""


def _is_comment_line(lines: Sequence[str], line_number: int) -> bool:
    """True when the line is a comment or docstring continuation."""
    if not 1 <= line_number <= len(lines):
        return False
    stripped = lines[line_number - 1].strip()
    return stripped.startswith(("#", "//", "*", "/*", '"""', "'''", "--"))


def rank_findings(findings: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most severe first, then by file and line, so the UI order is stable."""
    return sorted(
        findings,
        key=lambda finding: (
            SEVERITY_ORDER.get(str(finding.get("severity", "LOW")).upper(), 9),
            str(finding.get("file_path", "")),
            int(finding.get("line", 0) or 0),
        ),
    )


def summarize(findings: Sequence[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for finding in findings:
        severity = str(finding.get("severity", "LOW")).upper()
        counts[severity] = counts.get(severity, 0) + 1
    return {
        "total": len(findings),
        "by_severity": counts,
        "files_affected": len({str(finding.get("file_path", "")) for finding in findings if finding.get("file_path")}),
    }
