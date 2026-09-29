"""Turning a finding into a patch a developer can inspect.

The advice in a report is only useful if it can be acted on. For each finding
this module assembles the exact source around the problem, asks the model for a
unified diff that fixes that problem and nothing else, and then checks the diff
with ``git apply --check`` against the clone the analysis came from.

The check is the important part. A diff that does not apply is not advice, it is
decoration, so a failed check is fed back to the model once with the exact error
and, if the second attempt also fails, the fix is returned marked as not applying
rather than quietly dropped. Nothing in the clone is ever modified: the check is
read-only, and applying a patch is the developer's decision.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Sequence

from .chat_context import read_repo_file, resolve_repo_file
from .llm import ROLE_HEAVY, LLMError, get_llm_client
from .prompts import load_prompt

CONTEXT_RADIUS = 60
MAX_CONTEXT_BYTES = 12_000
APPLY_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 2


@dataclass
class ProposedFix:
    """A proposed change, and whether it actually applies to the clone."""

    finding_id: str
    file_path: str
    line: int
    diff: str = ""
    explanation: str = ""
    risk: str = "unknown"
    risk_notes: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    applies: bool = False
    validation: str = ""
    attempts: int = 0
    model: str = ""
    usage: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "file_path": self.file_path,
            "line": self.line,
            "diff": self.diff,
            "explanation": self.explanation,
            "risk": self.risk,
            "risk_notes": self.risk_notes,
            "alternatives": self.alternatives,
            "applies": self.applies,
            "validation": self.validation,
            "attempts": self.attempts,
            "model": self.model,
            "usage": self.usage,
        }


class FixAdvisor:
    """Proposes a validated patch for one finding in one stored snapshot."""

    def __init__(self, snapshot: dict[str, Any], client: Any | None = None) -> None:
        self.snapshot = snapshot
        self.repo_path = str(snapshot.get("repo_path", ""))
        self.client = client or get_llm_client()

    @property
    def available(self) -> bool:
        return self.client.configured and bool(self.repo_path)

    def propose(self, finding: dict[str, Any]) -> ProposedFix:
        """Ask for a patch, validate it, and report what came back."""
        file_path = str(finding.get("file_path", "")).strip()
        try:
            line = int(finding.get("line", 0) or 0)
        except (TypeError, ValueError):
            line = 0

        fix = ProposedFix(
            finding_id=str(finding.get("id", "")),
            file_path=file_path,
            line=line,
            model=self.client.model_for(ROLE_HEAVY),
        )
        if not self.available:
            fix.validation = "no model access configured"
            return fix

        source = read_repo_file(self.repo_path, file_path, MAX_CONTEXT_BYTES)
        if not source:
            fix.validation = f"{file_path} is not readable in the analysed clone"
            return fix

        region = _region(source, line)
        if not region.strip():
            fix.validation = f"line {line} is outside {file_path}"
            return fix

        feedback = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            fix.attempts = attempt
            payload = self._ask(finding, file_path, region, line, feedback)
            if payload is None:
                fix.validation = fix.validation or "the model did not return a usable patch"
                break

            fix.usage.append(payload["usage"])
            candidate = _clean_diff(str(payload["data"].get("diff", "")))
            if not candidate:
                fix.explanation = str(payload["data"].get("explanation", ""))[:1500]
                fix.validation = "the model explained the fix but produced no diff"
                break

            applies, reason = self._check(candidate)
            fix.diff = candidate
            fix.explanation = str(payload["data"].get("explanation", ""))[:1500]
            fix.risk = str(payload["data"].get("risk", "unknown")).strip().lower() or "unknown"
            fix.risk_notes = [str(note)[:200] for note in payload["data"].get("risk_notes", []) or []][:6]
            fix.alternatives = [str(note)[:200] for note in payload["data"].get("alternatives", []) or []][:4]
            fix.applies = applies
            fix.validation = reason

            if applies:
                break

            # Feed the exact failure back: a hunk that does not apply is usually
            # wrong line numbers or context, not a different fix.
            feedback = reason
        return fix

    # -- model ------------------------------------------------------------- #

    def _ask(
        self,
        finding: dict[str, Any],
        file_path: str,
        region: str,
        line: int,
        feedback: str,
    ) -> dict[str, Any] | None:
        facts = {
            "repository": self.snapshot.get("repo_url", ""),
            "file": file_path,
            "reported_line": line,
            "finding": {
                "name": finding.get("name", ""),
                "severity": finding.get("severity", ""),
                "description": finding.get("description", ""),
                "matched_line": str(finding.get("snippet", ""))[:200],
                "detector": finding.get("detector", ""),
            },
            "existing_advice": str(finding.get("recommendation", ""))[:600],
        }

        sections = [f"{load_prompt('fix_advisor_prompt.txt')}\n\n{facts}", f"File: {file_path}\n{region}"]
        if feedback:
            sections.append(
                f"Your previous diff did not apply. git said:\n{feedback}\n\n"
                "Return a corrected diff for this same file. Keep the change minimal and use the line numbers "
                "shown above."
            )

        try:
            data, result = self.client.chat_json_sync(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a senior engineer writing a minimal patch that fixes one reported problem. "
                            "You return JSON containing a unified diff that applies with git apply."
                        ),
                    },
                    {"role": "user", "content": "\n\n".join(sections)},
                ],
                role=ROLE_HEAVY,
                temperature=0.1,
                max_output_tokens=2000,
            )
        except LLMError as error:
            print(f"Fix advisor could not reach the model: {error}", flush=True)
            return None

        if not isinstance(data, dict):
            return None
        usage = result.usage_dict()
        usage["step"] = "fix_advisor"
        return {"data": data, "usage": usage}

    # -- validation -------------------------------------------------------- #

    def _check(self, diff: str) -> tuple[bool, str]:
        """``git apply --check`` the diff against the clone without touching it."""
        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            return False, "the analysed clone is not a git working tree, so the diff could not be checked"

        handle, patch_path = tempfile.mkstemp(prefix="codelens-fix-", suffix=".diff")
        try:
            with os.fdopen(handle, "w") as patch_file:
                patch_file.write(diff)
            completed = subprocess.run(
                ["git", "apply", "--check", "--whitespace=nowarn", patch_path],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=APPLY_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return False, f"git apply --check timed out after {APPLY_TIMEOUT_SECONDS}s"
        except OSError as error:
            return False, f"git apply --check could not run: {error}"
        finally:
            try:
                os.unlink(patch_path)
            except OSError:
                pass

        if completed.returncode == 0:
            return True, "git apply --check passed: this diff applies to the analysed commit"
        detail = (completed.stderr or completed.stdout or "git rejected the diff").strip()
        return False, detail[:400]


def _region(source: str, line: int, radius: int = CONTEXT_RADIUS) -> str:
    """The lines around the finding, with their real line numbers."""
    lines = source.splitlines()
    if not lines:
        return ""

    if line <= 0:
        line = 1
    start = max(0, line - 1 - radius)
    end = min(len(lines), line + radius)
    return "\n".join(f"{number}: {text}" for number, text in enumerate(lines[start:end], start=start + 1))


def _clean_diff(diff: str) -> str:
    """Accept a fenced diff or a bare one, and refuse anything that is not a diff."""
    text = (diff or "").strip()
    if "```" in text:
        start = text.find("```")
        newline = text.find("\n", start)
        if newline != -1:
            text = text[newline + 1 :]
        if "```" in text:
            text = text[: text.rfind("```")]

    lines = [line for line in text.strip("\n").splitlines() if line.strip()]
    if not lines or not any(line.startswith(("---", "+++", "@@", "diff --git")) for line in lines):
        return ""
    if not any(line.startswith("@@") for line in lines):
        return ""
    if _has_unsafe_path(lines):
        return ""

    return "\n".join(lines) + "\n"


def _has_unsafe_path(lines: Sequence[str]) -> bool:
    """Refuse a diff that reaches outside the repository or into its git directory.

    The diff is only ever checked, never applied, but ``git apply`` is run inside
    the clone, so a header naming ``/etc/passwd`` or ``.git/config`` is rejected
    before git is asked about it.
    """
    for line in lines:
        if not line.startswith(("--- ", "+++ ")):
            continue
        path = line[4:].strip().split("\t")[0]
        if path == "/dev/null":
            continue
        if path.startswith("/") or ".." in path.split("/") or ".git/" in f"{path}/":
            return True
    return False
