"""Git history signals for a cloned repository.

A shallow clone (which is what we deliberately make) contains a single commit,
so churn has to be deepened on demand and reported honestly when history is
unavailable. Every public method degrades to an empty result rather than raising:
a repository with no history is a normal situation, not an error.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_COMMITS = 120
DEFAULT_DEepen = 120


@dataclass
class FileChurn:
    """How much a file changed in the commits we inspected."""

    commits: int = 0
    added: int = 0
    removed: int = 0
    last_commit: str = ""
    _authors: set[str] = field(default_factory=set, repr=False)

    @property
    def total_changes(self) -> int:
        return self.added + self.removed

    @property
    def authors(self) -> int:
        return len({author for author in self._authors if author})

    def to_dict(self) -> dict[str, Any]:
        return {
            "commits": self.commits,
            "added": self.added,
            "removed": self.removed,
            "total_changes": self.total_changes,
            "authors": self.authors,
            "last_commit": self.last_commit,
        }

@dataclass
class ChurnReport:
    """Per-file churn plus the provenance of the numbers."""

    files: dict[str, FileChurn] = field(default_factory=dict)
    head: str = ""
    commits_scanned: int = 0
    history_depth: int = 0
    shallow: bool = False
    available: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "commits_scanned": self.commits_scanned,
            "history_depth": self.history_depth,
            "shallow": self.shallow,
            "available": self.available,
            "reason": self.reason,
        }


class GitHistory:
    """Reads commit history from a clone using the git CLI."""

    def __init__(self, repo_path: str, max_commits: int = DEFAULT_MAX_COMMITS, timeout: int = 30) -> None:
        self.repo_path = repo_path
        self.max_commits = max(1, max_commits)
        self.timeout = timeout

    def _git(self, *args: str) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                ["git", "-C", self.repo_path, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return 1, "", str(error)
        return completed.returncode, completed.stdout, completed.stderr

    # -- repository facts -------------------------------------------------- #

    def head_sha(self) -> str:
        code, out, _ = self._git("rev-parse", "HEAD")
        return out.strip() if code == 0 else ""

    def is_shallow(self) -> bool:
        code, out, _ = self._git("rev-parse", "--is-shallow-repository")
        return code == 0 and out.strip() == "true"

    def commit_count(self) -> int:
        code, out, _ = self._git("rev-list", "--count", "HEAD")
        try:
            return int(out.strip()) if code == 0 else 0
        except ValueError:
            return 0

    # -- history ----------------------------------------------------------- #

    def deepen(self, depth: int = DEFAULT_DEepen, timeout: int = 90) -> bool:
        """Fetch older commits so churn means something.

        ``--filter=blob:none`` is repeated explicitly so the fetch transfers
        commits and trees only, never file contents: enough for change counts,
        far too slow once diffs start pulling blobs.
        """
        if depth <= 0:
            return False
        try:
            completed = subprocess.run(
                ["git", "-C", self.repo_path, "fetch", "--deepen", str(depth), "--filter=blob:none", "--quiet", "origin"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def churn(self, deepen: int = 0, include_lines: bool = False) -> ChurnReport:
        """Per-file commit counts, authors and last-touched date.

        Commit counts come from ``git log --name-only``, which needs no blob
        content and therefore works on a partial clone. Line-level churn needs
        diffs, which means fetching blobs, so it is opt-in (``include_lines``).
        """
        report = ChurnReport(head=self.head_sha())

        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            report.reason = "not a git working tree"
            return report

        if deepen > 0 and self.is_shallow():
            if self.deepen(deepen):
                report.history_depth = deepen
            else:
                report.reason = "history could not be deepened; single-commit clone"

        report.shallow = self.is_shallow()

        log_args = [
            "log",
            f"-n{self.max_commits}",
            "--name-only",
            "--format=%x01%H%x02%an%x02%ad",
            "--date=short",
            "HEAD",
        ]
        code, out, err = self._git(*log_args)
        if code != 0:
            report.reason = f"git log failed: {err.strip()[:200]}"
            return report

        files: dict[str, FileChurn] = {}
        commits_seen = 0
        current_author = ""
        current_date = ""

        for line in out.splitlines():
            if line.startswith("\x01"):
                payload = line.lstrip("\x01")
                _, author, date = (payload.split("\x02", 2) + ["", ""])[:3]
                current_author, current_date = author, date
                commits_seen += 1
                continue

            path = line.strip()
            if not path:
                continue
            # Rename entries look like "old => new" or "dir/{a => b}/file.py".
            if " => " in path:
                path = path.split(" => ")[-1].replace("{", "").replace("}", "")
            entry = files.setdefault(path, FileChurn())
            entry.commits += 1
            if current_date and (not entry.last_commit or current_date > entry.last_commit):
                entry.last_commit = current_date
            entry._authors.add(current_author)  # type: ignore[attr-defined]

        report.files = files
        report.commits_scanned = commits_seen
        report.available = bool(files)
        if not files and not report.reason:
            report.reason = "no commit history available"
        if not report.history_depth and not report.shallow:
            report.history_depth = report.commits_scanned

        if include_lines and files:
            self._add_line_churn(report)

        return report

    def _add_line_churn(self, report: ChurnReport) -> None:
        """Fill in added/removed lines. Slow on a partial clone, hence opt-in."""
        code, out, err = self._git(
            "log",
            f"-n{self.max_commits}",
            "--numstat",
            "--format=%x01commit",
            "HEAD",
        )
        if code != 0:
            report.reason = f"line churn unavailable: {err.strip()[:160]}"
            return
        for line in out.splitlines():
            if not line or line.startswith("\x01") or "\t" not in line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            path = parts[2]
            if " => " in path:
                path = path.split(" => ")[-1].replace("{", "").replace("}", "")
            entry = report.files.get(path)
            if entry is None:
                continue
            if parts[0] == "-" or parts[1] == "-":
                continue
            try:
                entry.added += int(parts[0])
                entry.removed += int(parts[1])
            except ValueError:
                continue
