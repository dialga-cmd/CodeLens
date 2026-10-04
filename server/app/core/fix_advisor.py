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

import difflib
import os
import re
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
            if payload.get("truncated"):
                # Retrying at the same budget would truncate the same way.
                fix.usage.append(payload["usage"])
                fix.validation = "the model reached the token ceiling while reasoning and returned nothing"
                break

            fix.usage.append(payload["usage"])
            candidate = _clean_diff(str(payload["data"].get("diff", "")))
            if not candidate:
                fix.explanation = str(payload["data"].get("explanation", ""))[:1500]
                fix.validation = "the model explained the fix but produced no diff"
                break

            candidate = _reanchor(candidate, source)

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
                max_output_tokens=12000,
            )
        except LLMError as error:
            print(f"Fix advisor could not reach the model: {error}", flush=True)
            return None

        if result.finish_reason == "length":
            # The model spent its whole budget reasoning. Retrying at the same
            # size would truncate the same way, so this is reported, not hidden.
            print(
                "Fix advisor hit the token ceiling before emitting a patch "
                f"({result.completion_tokens} tokens); raise NEBIUS_MAX_OUTPUT_TOKENS",
                flush=True,
            )
            usage = result.usage_dict()
            usage["step"] = "fix_advisor"
            return {"data": None, "usage": usage, "truncated": True}
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

    raw = text.strip("\n").splitlines()

    # In a unified diff a blank context line is a single space, and a blank added
    # or removed line is a bare "+" or "-". Dropping whitespace-only lines deletes
    # real content from the patch: the hunk header counts stop matching the body
    # and git reports the patch as corrupt. Only genuinely empty lines from
    # prose around the diff are dropped.
    lines = [line for line in raw if line.strip() or line in {"+", "-", " "}]
    if not lines or not any(line.startswith(("---", "+++", "@@", "diff --git")) for line in lines):
        return ""
    if not any(line.startswith("@@") for line in lines):
        return ""
    if _has_unsafe_path(lines):
        return ""

    return "\n".join(_recount(lines)) + "\n"


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def _reanchor(diff: str, source: str) -> str:
    """Rebuild the patch against the real file so its context is the file's context.

    A model writes the lines it changes and approximates the lines around them.
    The changes are the judgement worth keeping; the surrounding context is
    bookkeeping it gets wrong in the same way every time - the live Flask patch
    dropped the two blank lines between the function and the class below it, so
    ``git apply`` could not find its own context even with correct counts.

    So the removed lines are located in the file, the added lines are spliced in
    there, and ``difflib`` writes the hunk. If the removed lines are not in the
    file the model's own diff is returned untouched, so ``git apply`` still gets
    to refuse it and the failure is reported rather than papered over.
    """
    lines = diff.splitlines()
    edits: list[tuple[list[str], list[str]]] = []
    current: tuple[list[str], list[str]] | None = None

    for line in lines:
        if line.startswith(("--- ", "+++ ", "diff --git")):
            current = None
            continue
        if line.startswith("@@"):
            current = ([], [])
            edits.append(current)
            continue
        if current is None:
            continue
        if line.startswith("-"):
            current[0].append(line[1:])
        elif line.startswith("+"):
            current[1].append(line[1:])
        # Context lines are dropped on purpose. They are the part the model gets
        # wrong, and including them in the block would make the block unfindable
        # in exactly the cases this exists to fix. difflib regenerates them.

    if not edits:
        return diff

    file_lines = source.splitlines()
    replacements = [(removed, added) for removed, added in edits if removed and added]
    if not replacements:
        return diff

    # Longest block first: an earlier short match inside a long block would
    # otherwise consume lines the long block needed.
    replacements.sort(key=lambda pair: len(pair[0]), reverse=True)
    rebuilt = list(file_lines)
    consumed: set[int] = set()
    for removed, added in replacements:
        span = len(removed)
        start = _find_block(rebuilt, removed, skip=consumed)
        if start is None:
            return diff
        rebuilt[start : start + span] = added
        consumed.update(range(start, start + span))

    if rebuilt == file_lines:
        return diff

    target = ""
    for line in lines[:2]:
        if line.startswith(("--- ", "+++ ")):
            candidate = line[4:].strip().split("\t")[0]
            for prefix in ("a/", "b/"):
                if candidate.startswith(prefix):
                    candidate = candidate[len(prefix) :]
                    break
            target = candidate
            break
    if not target:
        return diff

    generated = difflib.unified_diff(
        file_lines,
        rebuilt,
        fromfile=f"a/{target}",
        tofile=f"b/{target}",
        n=3,
        lineterm="",
    )
    body = [line for line in generated if not line.startswith(("---", "+++"))]
    if not body:
        return diff
    return "\n".join([f"--- a/{target}", f"+++ b/{target}", *body]) + "\n"


def _find_block(lines: Sequence[str], block: Sequence[str], skip: set[int] | None = None) -> int | None:
    """The index where ``block`` appears in ``lines`` contiguously, or None."""
    skip = skip or set()
    span = len(block)
    for start in range(len(lines) - span + 1):
        if skip & set(range(start, start + span)):
            continue
        if list(lines[start : start + span]) == list(block):
            return start
    return None


def _recount(lines: Sequence[str]) -> list[str]:
    """Rewrite each hunk header's counts from the lines that actually follow it.

    The header count is arithmetic the model does by hand, and it gets it wrong:
    the fix-advisor diff for Flask's ``_lazy_sha1`` declared ``@@ -273,11 +273,11 @@``
    over a body of eight lines, which git rejects as a corrupt patch before it ever
    looks at the file. The counts are not the model's judgement, they are a
    property of its own output, so they are recomputed here rather than asked for.
    The start lines and the section heading are left exactly as written.
    """
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        header = _HUNK_HEADER.match(line)
        if not header:
            output.append(line)
            index += 1
            continue

        body: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].startswith(("@@", "--- ", "+++ ", "diff --git")):
            if lines[cursor][:1] in {" ", "+", "-", "\\"}:
                body.append(lines[cursor])
            cursor += 1

        old_count = sum(1 for entry in body if entry[:1] in {" ", "-"})
        new_count = sum(1 for entry in body if entry[:1] in {" ", "+"})
        old_start = int(header.group(1))
        new_start = int(header.group(3)) or old_start
        output.append(
            f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{header.group(5)}"
        )
        output.extend(body)
        index = cursor
    return output


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
