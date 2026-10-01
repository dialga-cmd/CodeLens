"""Getting a repository onto disk, and keeping what was learned about it.

Two responsibilities live here, and they both exist because the API is public:

* **cloning** one GitHub repository, safely. The URL is validated against a
  strict pattern rather than trusted, the clone is bounded in time and in size,
  and a repository that is too large is removed again before anything tries to
  parse it.
* **storing** the analysis, durably. Snapshots used to live only in a temporary
  directory that a restart or a container recycle would empty, which made the
  chat and fix endpoints fail for a repository the user had just analysed. They
  now go to ``CODELENS_DATA_DIR``, are written atomically so a half-written file
  can never be read, and are expired on a time-to-live with their clone.

The registry is an index, never the source of truth: if it is lost the snapshot
files are still found by name, so a corrupt index degrades the cache instead of
breaking the API.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import git

DEFAULT_DATA_DIR = os.path.join(tempfile.gettempdir(), "codelens_repos")

# GitHub owner and repository names: letters, digits and . _ - between two
# alphanumeric ends. Nothing else reaches the filesystem or the git command.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
RESERVED_NAMES = {"", ".", ".."}

MAX_REPO_MB = 300
CLONE_TIMEOUT_SECONDS = 300
REMOTE_TIMEOUT_SECONDS = 20
SNAPSHOT_TTL_HOURS = 24
MAX_SNAPSHOTS = 40


class RepoURLError(ValueError):
    """The URL is not a GitHub repository we are willing to clone."""


class RepoTooLargeError(ValueError):
    """The clone exceeded the per-repository size budget."""


# `git@github.com:owner/repo.git` is what GitHub's "Copy clone URL" button hands
# over, and `www.github.com` is what some people have in their address bar.
SCP_LIKE = re.compile(r"^(?:git@)?github\.com:(?P<path>[^/]+/[^/]+?)(?:\.git)?/?$", re.IGNORECASE)
ALLOWED_HOSTS = {"github.com", "www.github.com"}


def normalize_repo_url(repo_url: str) -> str:
    """Return the canonical ``https://github.com/owner/repo`` form of a URL.

    Rejects what cannot be a public GitHub repository rather than passing it to
    git: other hosts, credentials in the URL, ports, query strings, fragments,
    path traversal and anything that is not exactly two path segments.

    Owner and repository are lowercased because GitHub treats them as
    case-insensitive, and the repo id is derived from this string: without it
    ``github.com/Pallets/Flask`` would be cached separately from
    ``github.com/pallets/flask`` and every analysis would be repeated.
    """
    candidate = (repo_url or "").strip()
    if not candidate:
        raise RepoURLError("A repository URL is required.")

    scp = SCP_LIKE.match(candidate)
    if scp:
        candidate = f"https://github.com/{scp.group('path')}"

    parsed = urlparse(candidate)
    if parsed.scheme != "https":
        raise RepoURLError("Only https:// repository URLs are supported.")
    if parsed.username or parsed.password:
        raise RepoURLError("Credentials must not be part of the repository URL.")
    if parsed.hostname is None or parsed.hostname.lower() not in ALLOWED_HOSTS:
        raise RepoURLError("Only github.com repositories are supported.")
    try:
        port = parsed.port
    except ValueError:
        # urlparse only rejects a non-numeric port when the attribute is read,
        # so this is where `github.com:notaport/repo` has to be turned away.
        raise RepoURLError("The repository URL must not specify a port.") from None
    if port:
        raise RepoURLError("The repository URL must not specify a port.")
    if parsed.query or parsed.fragment:
        raise RepoURLError("The repository URL must not carry a query string or fragment.")

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2:
        raise RepoURLError("The URL must be https://github.com/<owner>/<repository>.")
    owner, repository = segments[0].lower(), segments[1].lower()
    if repository.endswith(".git"):
        repository = repository[:-4]

    for label, value in (("owner", owner), ("repository name", repository)):
        if value in RESERVED_NAMES or not NAME_PATTERN.match(value):
            raise RepoURLError(f"The {label} may only contain letters, digits, dots, dashes and underscores.")

    return f"https://github.com/{owner}/{repository}"


class IngestionEngine:
    """Clones repositories and stores the analysis of what was found."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = base_dir or os.getenv("CODELENS_DATA_DIR") or DEFAULT_DATA_DIR
        os.makedirs(self.base_dir, exist_ok=True)
        self.registry_path = os.path.join(self.base_dir, "_registry.json")
        self.index_path = os.path.join(self.base_dir, "_index.json")
        self.max_repo_bytes = int(float(os.getenv("CODELENS_MAX_REPO_MB", str(MAX_REPO_MB))) * 1024 * 1024)
        self.clone_timeout = int(os.getenv("CODELENS_CLONE_TIMEOUT", str(CLONE_TIMEOUT_SECONDS)))
        self.remote_timeout = int(os.getenv("CODELENS_REMOTE_TIMEOUT", str(REMOTE_TIMEOUT_SECONDS)))
        self.snapshot_ttl = int(float(os.getenv("CODELENS_SNAPSHOT_TTL_HOURS", str(SNAPSHOT_TTL_HOURS))) * 3600)
        self.max_snapshots = max(1, int(os.getenv("CODELENS_MAX_SNAPSHOTS", str(MAX_SNAPSHOTS))))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # identity
    # ------------------------------------------------------------------ #

    def repo_id_for(self, repo_url: str) -> str:
        """A stable, filesystem-safe id for a repository URL.

        The URL is canonicalised first, so ``repo`` and ``repo.git`` are the same
        repository with the same id and therefore the same cached analysis.
        """
        try:
            canonical = normalize_repo_url(repo_url)
        except RepoURLError:
            canonical = (repo_url or "").strip()
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    def _clone_path(self, repo_url: str) -> str:
        repo_id = self.repo_id_for(repo_url)
        repository = repo_url.rstrip("/").split("/")[-1]
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", repository)[:40] or "repo"
        return os.path.join(self.base_dir, f"{safe_name}_{repo_id}")

    def _snapshot_path(self, repo_id: str) -> str:
        return os.path.join(self.base_dir, f"{repo_id}.snapshot.json")

    # ------------------------------------------------------------------ #
    # cloning
    # ------------------------------------------------------------------ #

    def clone_repo(self, repo_url: str, report: Callable[[str], None] | None = None) -> str:
        """Clone one repository, bounded in time and size.

        The clone is shallow: history is deepened later, only if the analysis
        actually needs commit frequency, because downloading every blob of a
        large project buys nothing the first pass uses.
        """
        canonical = normalize_repo_url(repo_url)
        target_path = self._clone_path(canonical)
        if os.path.exists(target_path):
            shutil.rmtree(target_path, ignore_errors=True)

        message = f"Cloning {canonical} into {target_path}..."
        if report:
            report(message)
        else:
            print(message, flush=True)

        try:
            git.Repo.clone_from(
                canonical,
                target_path,
                depth=1,
                kill_after_timeout=self.clone_timeout,
                env={
                    # Never block waiting for a credential prompt on a private or
                    # missing repository: fail and report instead.
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": "echo",
                },
            )
        except git.GitCommandError as error:
            shutil.rmtree(target_path, ignore_errors=True)
            raise RepoURLError(f"git could not clone {canonical}: {_git_message(error)}") from error
        except Exception as error:  # noqa: BLE001 - GitPython raises several types here
            shutil.rmtree(target_path, ignore_errors=True)
            raise RepoURLError(f"git could not clone {canonical}: {error}") from error

        self._enforce_size_limit(target_path, canonical)
        return target_path

    def _enforce_size_limit(self, target_path: str, repo_url: str) -> None:
        size = directory_size(target_path)
        if size <= self.max_repo_bytes:
            return
        shutil.rmtree(target_path, ignore_errors=True)
        raise RepoTooLargeError(
            f"{repo_url} is {size / (1024 * 1024):.0f} MB, over the "
            f"{self.max_repo_bytes / (1024 * 1024):.0f} MB limit for this deployment."
        )

    def remote_head(self, repo_url: str) -> str:
        """The current commit on the default branch, or ``""`` if it cannot be read.

        Used only to decide whether a stored analysis is still current. A failure
        here is not an error: it just means the repository is analysed again.
        """
        canonical = normalize_repo_url(repo_url)
        try:
            completed = subprocess.run(
                ["git", "ls-remote", canonical, "HEAD"],
                capture_output=True,
                text=True,
                timeout=self.remote_timeout,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"},
            )
        except (subprocess.TimeoutExpired, OSError):
            return ""
        if completed.returncode != 0:
            return ""
        first = (completed.stdout or "").strip().splitlines()
        if not first:
            return ""
        return first[0].split("\t")[0].strip()

    def cleanup(self, repo_path: str) -> None:
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # snapshots
    # ------------------------------------------------------------------ #

    def save_snapshot(self, repo_id: str, snapshot: dict[str, Any]) -> None:
        """Write the snapshot atomically, then index it by URL and commit."""
        snapshot_path = self._snapshot_path(repo_id)
        _write_json_atomic(snapshot_path, snapshot)

        with self._lock:
            registry = _read_json(self.registry_path)
            registry[repo_id] = {
                "path": snapshot_path,
                "repo_url": snapshot.get("repo_url", ""),
                "head_sha": snapshot.get("head_sha", ""),
                "analyzed_at": snapshot.get("analyzed_at", ""),
            }
            _write_json_atomic(self.registry_path, registry)

            index = _read_json(self.index_path)
            index[snapshot.get("repo_url", "")] = {
                "repo_id": repo_id,
                "head_sha": snapshot.get("head_sha", ""),
                "analyzed_at": snapshot.get("analyzed_at", ""),
            }
            _write_json_atomic(self.index_path, index)

        self.expire(keep_repo_id=repo_id)

    def load_snapshot(self, repo_id: str) -> dict[str, Any] | None:
        """Read a snapshot by id, falling back to its file name if the index is gone."""
        registry = _read_json(self.registry_path)
        entry = registry.get(repo_id)
        snapshot_path = entry.get("path") if isinstance(entry, dict) else None
        if not snapshot_path or not os.path.exists(snapshot_path):
            snapshot_path = self._snapshot_path(repo_id)
        if not os.path.exists(snapshot_path):
            return None

        snapshot = _read_json(snapshot_path)
        return snapshot or None

    def cached_snapshot(self, repo_url: str) -> dict[str, Any] | None:
        """A stored analysis of this repository at the commit it currently points at.

        Returns ``None`` when the commit moved, the snapshot is gone, or the clone
        the snapshot was built from has been expired - in which case the caller
        analyses the repository again.
        """
        try:
            canonical = normalize_repo_url(repo_url)
        except RepoURLError:
            return None

        index = _read_json(self.index_path)
        entry = index.get(canonical)
        if not isinstance(entry, dict):
            return None
        if entry.get("head_sha") != self.remote_head(canonical) or not entry.get("head_sha"):
            return None

        snapshot = self.load_snapshot(str(entry.get("repo_id", "")))
        if not snapshot or snapshot.get("truncated"):
            # A truncated analysis is a partial answer; do not make it sticky.
            return None
        if not os.path.isdir(str(snapshot.get("repo_path", ""))):
            return None
        return snapshot

    def expire(self, keep_repo_id: str = "") -> list[str]:
        """Delete snapshots and clones past the time-to-live, newest last.

        The clone is removed with its snapshot, because a snapshot without the
        files it describes breaks ``/repo/file`` and the chat tools. The snapshot
        currently being written is never a candidate.
        """
        cutoff = time.time() - self.snapshot_ttl
        removed: list[str] = []

        candidates: list[tuple[float, str, str]] = []
        for name in os.listdir(self.base_dir):
            if not name.endswith(".snapshot.json"):
                continue
            path = os.path.join(self.base_dir, name)
            try:
                modified = os.path.getmtime(path)
            except OSError:
                continue
            candidates.append((modified, name[: -len(".snapshot.json")], path))

        # Anything past its time-to-live, plus the oldest beyond the count cap.
        candidates.sort(key=lambda item: item[0])
        excess = {repo_id for _modified, repo_id, _path in candidates[: max(0, len(candidates) - self.max_snapshots)]}
        for modified, repo_id, path in candidates:
            if repo_id == keep_repo_id:
                continue
            if modified > cutoff and repo_id not in excess:
                continue

            snapshot = _read_json(path)
            repo_path = str(snapshot.get("repo_path", "")) if snapshot else ""
            if repo_path and repo_path != self.base_dir and os.path.isdir(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)
            try:
                os.remove(path)
            except OSError:
                continue
            removed.append(repo_id)

        if removed:
            self._forget(removed)
        return removed

    def _forget(self, repo_ids: Iterable[str]) -> None:
        forgotten = set(repo_ids)
        with self._lock:
            registry = _read_json(self.registry_path)
            for repo_id in forgotten:
                registry.pop(repo_id, None)
            _write_json_atomic(self.registry_path, registry)

            index = _read_json(self.index_path)
            for url in [url for url, entry in index.items() if isinstance(entry, dict) and entry.get("repo_id") in forgotten]:
                index.pop(url, None)
            _write_json_atomic(self.index_path, index)

    def stats(self) -> dict[str, Any]:
        """What is stored right now: used by ``/health`` and by the tests."""
        snapshots = [name for name in os.listdir(self.base_dir) if name.endswith(".snapshot.json")]
        clones = [
            name
            for name in os.listdir(self.base_dir)
            if os.path.isdir(os.path.join(self.base_dir, name))
        ]
        return {
            "data_dir": self.base_dir,
            "snapshots": len(snapshots),
            "clones": len(clones),
            "bytes": directory_size(self.base_dir),
            "max_repo_bytes": self.max_repo_bytes,
            "snapshot_ttl_seconds": self.snapshot_ttl,
            "max_snapshots": self.max_snapshots,
        }


def directory_size(path: str) -> int:
    """Bytes on disk under ``path``, ignoring files that vanish mid-walk."""
    total = 0
    for root, _directories, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _write_json_atomic(path: str, payload: Any) -> None:
    """Write JSON through a temporary file so readers never see a partial one."""
    handle, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(payload, output)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _git_message(error: git.GitCommandError) -> str:
    text = (getattr(error, "stderr", "") or getattr(error, "stdout", "") or str(error)).strip()
    return " ".join(text.split())[:300]
