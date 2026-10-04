"""Keeping one public deployment usable by several people at once.

Analysis is expensive: a clone, a history walk and a set of model calls. Chat and
the fix advisor are cheaper but not free. On a demo instance that judges share,
the difference between "one person analysing three repositories" and "twenty
people analysing the same repository at once" is the difference between a working
demo and an instance that runs out of memory.

Two mechanisms, both deliberately simple:

* :class:`RateLimiter` is a token bucket per client and per class of endpoint.
  The limit is set from the environment, the caller gets a ``429`` with a
  ``Retry-After``, and nothing else changes.
* :class:`AnalysisRegistry` collapses identical concurrent work. Two requests for
  the same repository share one run: the second attaches to the progress stream
  of the first and receives the same result, rather than cloning the repository
  twice and spending the same model budget twice.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# Default limits, as "requests/window_seconds". An analysis is measured in
# minutes, chat in seconds, so the classes are not interchangeable.
DEFAULT_LIMITS = {
    "analyze": (12, 3600),
    "chat": (240, 3600),
    "fix": (40, 3600),
    "file": (600, 3600),
}


def parse_limit(value: str | None, fallback: tuple[int, int]) -> tuple[int, int]:
    """Read a ``"count/window"`` limit, ignoring anything malformed.

    The suffix ``describe()`` prints is accepted, because that string is what a
    reader copies out of ``/health`` and pastes into the environment. Rejecting
    it would make the limiter silently keep its default with no warning anywhere,
    which is the worst way for a limit to misbehave.
    """
    if not value:
        return fallback
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*([smh]?)\s*", value.strip().lower())
    if not match:
        return fallback
    count, window, unit = int(match.group(1)), int(match.group(2)), match.group(3)
    window *= {"s": 1, "m": 60, "h": 3600, "": 1}[unit]
    return (count, window) if count > 0 and window > 0 else fallback


@dataclass
class Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """One token bucket per client, refilled continuously."""

    def __init__(self, count: int, window: int) -> None:
        self.capacity = float(count)
        self.window = float(window)
        self.refill_per_second = self.capacity / self.window
        self._buckets: dict[str, Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Take ``cost`` tokens for ``key``.

        Returns ``(allowed, retry_after_seconds)``. A negative time means the
        request was allowed and the caller should not retry.
        """
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = Bucket(tokens=self.capacity, updated=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_second)
            bucket.updated = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, -1.0

            missing = cost - bucket.tokens
            wait = missing / self.refill_per_second if self.refill_per_second else float(self.window)
        return False, max(1.0, wait)

    def prune(self, max_age: float = 3600.0) -> int:
        """Forget idle buckets so a long-running process does not grow without bound."""
        now = time.monotonic()
        with self._lock:
            stale = [key for key, bucket in self._buckets.items() if now - bucket.updated > max_age]
            for key in stale:
                self._buckets.pop(key, None)
        return len(stale)

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "capacity": self.capacity,
                "window_seconds": self.window,
                "tracked_clients": len(self._buckets),
            }


class RateLimits:
    """The limiters for every class of endpoint, built once from the environment."""

    def __init__(self) -> None:
        enabled = os.getenv("CODELENS_RATE_LIMIT", "true").lower() in {"1", "true", "yes", "on"}
        self.enabled = enabled
        self.trust_proxy = os.getenv("CODELENS_TRUST_PROXY", "true").lower() in {"1", "true", "yes", "on"}
        self.limiters = {
            name: RateLimiter(*parse_limit(os.getenv(f"CODELENS_RATE_{name.upper()}"), default))
            for name, default in DEFAULT_LIMITS.items()
        }

    def check(self, kind: str, client: str) -> tuple[bool, float]:
        limiter = self.limiters.get(kind)
        if not self.enabled or limiter is None:
            return True, -1.0
        return limiter.allow(f"{kind}:{client}")

    def client_key(self, request: Any) -> str:
        """Identify the caller.

        Behind a reverse proxy the socket address is the proxy, so
        ``X-Forwarded-For`` is preferred when ``CODELENS_TRUST_PROXY`` is on. The
        header is only used as an opaque bucket key, never logged or stored.
        """
        if self.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[0].strip()[:64]
        client = getattr(request, "client", None)
        return getattr(client, "host", "") or "unknown"

    def prune(self) -> int:
        return sum(limiter.prune() for limiter in self.limiters.values())

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "trust_proxy": self.trust_proxy,
            "limits": {name: f"{int(l.capacity)}/{int(l.window)}s" for name, l in self.limiters.items()},
        }


class TooManyAnalyses(RuntimeError):
    """The instance is already running as many analyses as it will run at once."""


@dataclass
class AnalysisJob:
    """One analysis run, with any number of subscribers watching it.

    The run itself happens on a thread because a clone and a history walk are
    blocking, and the HTTP layer only reads from the subscribers' queues.
    """

    repo_url: str
    runner: Callable[[Callable[[str], None]], dict[str, Any]]
    messages: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str = ""
    finished: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.time)
    _subscribers: list[queue.Queue[str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"analysis-{self.repo_id}", daemon=True).start()

    @property
    def repo_id(self) -> str:
        return str((self.result or {}).get("repo_id", "")) or "pending"

    def _run(self) -> None:
        try:
            self.result = self.runner(self.publish)
        except Exception as error:  # noqa: BLE001 - the reason is reported to every subscriber
            self.error = f"{type(error).__name__}: {error}".strip()
        finally:
            self.finished.set()

    def publish(self, message: str) -> None:
        with self._lock:
            self.messages.append(message)
            subscribers = list(self._subscribers)
        for target in subscribers:
            target.put(message)

    def subscribe(self) -> queue.Queue[str]:
        """A queue pre-loaded with the messages already emitted.

        A subscriber that joins half-way through therefore sees the whole run,
        not just its own tail of it.
        """
        target: queue.Queue[str] = queue.Queue()
        with self._lock:
            for message in self.messages:
                target.put(message)
            self._subscribers.append(target)
        return target

    def unsubscribe(self, target: queue.Queue[str]) -> None:
        with self._lock:
            if target in self._subscribers:
                self._subscribers.remove(target)

    @property
    def running(self) -> bool:
        return not self.finished.is_set()

    @property
    def age(self) -> float:
        return time.time() - self.started_at


class AnalysisRegistry:
    """Deduplicates concurrent analyses of the same repository."""

    def __init__(self, max_running: int | None = None) -> None:
        self.max_running = max(1, int(os.getenv("CODELENS_MAX_CONCURRENT_ANALYSES", str(max_running or 4))))
        self.grace_seconds = int(os.getenv("CODELENS_JOB_GRACE_SECONDS", "300"))
        self._jobs: dict[str, AnalysisJob] = {}
        self._lock = threading.Lock()

    def start_or_attach(
        self,
        repo_url: str,
        runner: Callable[[Callable[[str], None]], dict[str, Any]],
    ) -> tuple[AnalysisJob, bool]:
        """Return the job for this repository, starting it if nobody else has.

        The boolean says whether this caller started the run.
        """
        with self._lock:
            self._prune_locked()
            existing = self._jobs.get(repo_url)
            if existing is not None and (existing.running or existing.age < self.grace_seconds):
                return existing, False

            running = sum(1 for job in self._jobs.values() if job.running)
            if running >= self.max_running:
                raise TooManyAnalyses(
                    f"This instance is already analysing {running} repositories. Try again in a minute."
                )

            job = AnalysisJob(repo_url=repo_url, runner=runner)
            self._jobs[repo_url] = job
            job.start()
            return job, True

    def get(self, repo_url: str) -> AnalysisJob | None:
        with self._lock:
            return self._jobs.get(repo_url)

    def _prune_locked(self) -> None:
        for repo_url, job in list(self._jobs.items()):
            if not job.running and job.age > self.grace_seconds:
                self._jobs.pop(repo_url, None)

    def describe(self) -> dict[str, Any]:
        with self._lock:
            running = [job.repo_url for job in self._jobs.values() if job.running]
            recent = [
                {"repo_url": job.repo_url, "seconds": round(job.age, 1), "error": job.error}
                for job in self._jobs.values()
                if not job.running
            ]
        return {"running": running, "max_running": self.max_running, "finished": recent}
