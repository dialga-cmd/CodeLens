"""Grounding the report in sources outside the repository.

Static analysis can prove that a pattern is dangerous; it cannot know whether the
package a project pinned in 2019 has a published advisory, or what the current
maintainers recommend for the class of bug the scanner flagged. Both questions
have a real answer somewhere on the web, and this module fetches it through the
Tavily Search API.

The rule that keeps this honest: a search result is **evidence to cite, not a
verdict**. A dependency is only called vulnerable when the returned text names
both the package and a CVE identifier, and even then the entry carries the quoted
line and the links so a developer can check the affected version range. When the
answer cannot be parsed the dependency is reported as unverified rather than
guessed at, and when no key is configured nothing is searched and the UI says so.

Two things are grounded:

* every dependency that the manifests pin, checked for published advisories and,
  for the most important of them, for the current version;
* the highest-severity findings, which get a link to the authoritative guidance
  for their class of bug so the model-written remediation is not the last word.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import httpx

TAVILY_ENDPOINT = "https://api.tavily.com/search"

# Authoritative guidance per finding class. Restricting to these domains keeps a
# remediation link pointing at a specification rather than a blog post; if the
# restricted search returns nothing the engine retries unrestricted.
GUIDANCE_DOMAINS = ["owasp.org", "cheatsheetseries.owasp.org", "cwe.mitre.org", "nvd.nist.gov"]

GROUNDING_QUERIES: dict[str, str] = {
    "hardcoded_secret": "hardcoded credentials in source code remediation secret management OWASP",
    "high_entropy_value": "detecting and rotating leaked API keys and secrets OWASP",
    "shell_injection": "OS command injection prevention subprocess shell false OWASP",
    "sql_injection": "SQL injection prevention parameterised queries OWASP",
    "path_traversal": "path traversal prevention directory traversal OWASP",
    "weak_hash": "password hashing recommendation argon2 scrypt OWASP",
    "insecure_random": "cryptographically secure random number generation OWASP",
    "unsafe_deserialization": "insecure deserialization pickle yaml load OWASP",
    "insecure_eval": "code injection eval exec dynamic code execution OWASP",
    "disabled_tls_verification": "disabling TLS certificate verification risks OWASP",
    "xss": "cross-site scripting prevention output encoding OWASP",
    "open_redirect": "open redirect prevention unvalidated redirects OWASP",
    "cors_wildcard": "CORS misconfiguration wildcard origins OWASP",
    "unsigned_jwt": "JSON web token verification algorithm confusion OWASP",
}

_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
_VERSION_RE = re.compile(r"\b(\d+(?:\.\d+){0,3}(?:[-.]?(?:a|b|rc|alpha|beta)\.?\d+)?)\b", re.IGNORECASE)
_FIXED_IN_RE = re.compile(
    r"(?:fixed in|patched in|fixed from|upgrade to|update to|remedied in)\D{0,24}(\d+(?:\.\d+){1,3})",
    re.IGNORECASE,
)
_SNIPPET_RE = re.compile(r"^(?:vulnerab\w*|affected|exploit\w*|security|remote code|outdated|deprecated)", re.IGNORECASE)

SEVERITY_KEYWORDS = (
    ("CRITICAL", ("critical", "remote code execution", "rce", "arbitrary code")),
    ("HIGH", ("high severity", "high-severity", "privilege escalation", "sql injection", "command injection")),
    ("MEDIUM", ("moderate", "medium", "denial of service", "information disclosure", "xss", "csrf")),
    ("LOW", ("low severity", "low-severity", "minor", "information leak")),
)

# Ecosystems whose version strings mean different things.
ECOSYSTEM_LABELS = {
    "pypi": "PyPI",
    "npm": "npm",
    "go": "Go modules",
    "crates.io": "crates.io",
    "rubygems": "RubyGems",
    "packagist": "Packagist",
}


class TavilyError(RuntimeError):
    """A Tavily request that did not return usable results."""


@dataclass
class Source:
    title: str
    url: str
    snippet: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title[:200],
            "url": self.url[:500],
            "snippet": self.snippet[:400],
            "score": round(self.score, 3),
        }


@dataclass
class TavilyResponse:
    query: str
    answer: str = ""
    sources: list[Source] = field(default_factory=list)
    latency_s: float = 0.0
    request_id: str = ""

    @property
    def text(self) -> str:
        """Everything the search returned, as one searchable blob."""
        return "\n".join([self.answer, *(source.snippet for source in self.sources)])

    def top_sources(self, limit: int = 3) -> list[dict[str, Any]]:
        return [source.to_dict() for source in self.sources[:limit]]


class TavilyClient:
    """Thin, synchronous wrapper over ``POST https://api.tavily.com/search``."""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")).strip()
        self.endpoint = endpoint or os.getenv("TAVILY_API_ENDPOINT", TAVILY_ENDPOINT)
        self.timeout = timeout or float(os.getenv("TAVILY_TIMEOUT", "25"))
        self.max_retries = max_retries if max_retries is not None else int(os.getenv("TAVILY_MAX_RETRIES", "2"))
        self.searches_run = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        query: str,
        *,
        search_depth: str = "advanced",
        max_results: int = 5,
        include_answer: bool = True,
        topic: str = "general",
        include_domains: Sequence[str] | None = None,
    ) -> TavilyResponse:
        """Run one search. Raises :class:`TavilyError` when nothing usable comes back."""
        if not self.available:
            raise TavilyError("TAVILY_API_KEY is not set")

        payload: dict[str, Any] = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max(1, min(int(max_results), 20)),
            "include_answer": include_answer,
            "topic": topic,
            "include_usage": True,
        }
        if include_domains:
            payload["include_domains"] = list(include_domains)
            payload["include_domains_mode"] = "prefer"

        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                response = httpx.post(
                    self.endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout,
                )
            except httpx.HTTPError as error:
                last_error = error
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last_error = TavilyError(f"HTTP {response.status_code}: {_safe_error(response)}")
                if attempt < self.max_retries:
                    time.sleep(_retry_after(response, attempt))
                continue
            if response.status_code >= 400:
                raise TavilyError(f"HTTP {response.status_code}: {_safe_error(response)}")

            data = response.json()
            if not isinstance(data, dict):
                raise TavilyError("Tavily returned a response that was not a JSON object")

            self.searches_run += 1
            return TavilyResponse(
                query=query,
                answer=str(data.get("answer") or ""),
                sources=[_source_from(item) for item in (data.get("results") or []) if isinstance(item, dict)],
                latency_s=round(time.perf_counter() - started, 3),
                request_id=str(data.get("request_id") or ""),
            )

        raise TavilyError(str(last_error) if last_error else "Tavily request failed")


class ResearchEngine:
    """Uses web search to check dependencies and to source the top findings."""

    def __init__(self, client: TavilyClient | None = None, max_concurrency: int = 4) -> None:
        self.client = client or TavilyClient()
        self.max_concurrency = max(1, int(os.getenv("CODELENS_RESEARCH_CONCURRENCY", str(max_concurrency))))

    @property
    def available(self) -> bool:
        return self.client.available

    # -- dependencies ------------------------------------------------------ #

    def verify_dependencies(
        self,
        manifests: Sequence[dict[str, Any]],
        *,
        advisory_limit: int | None = None,
        version_limit: int | None = None,
    ) -> dict[str, Any]:
        """Check pinned dependencies for published advisories and current versions."""
        advisory_budget = int(os.getenv("CODELENS_TAVILY_ADVISORY_QUERIES", "8")) if advisory_limit is None else advisory_limit
        version_budget = int(os.getenv("CODELENS_TAVILY_VERSION_QUERIES", "5")) if version_limit is None else version_limit

        candidates = self._dependency_candidates(manifests)
        report: dict[str, Any] = {
            "provider": "tavily",
            "enabled": self.available,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "vulnerable": [],
            "outdated": [],
            "checked": [],
            "unverified": [],
            "queries_run": 0,
            "declared_dependencies": len(candidates),
            "not_checked": 0,
            "errors": [],
            "note": (
                "Advisories come from web search, not from a vulnerability database. "
                "Confirm the affected version range in the linked advisory before acting."
            ),
        }
        if not self.available:
            report["note"] = "TAVILY_API_KEY is not set, so no dependency was checked against published advisories."
            return report
        if not candidates:
            return report

        advisory_targets = candidates[:advisory_budget]
        version_targets = candidates[:version_budget]

        # An advisory search is worth an advanced query; asking which version is
        # current is a single-fact question and does not need the extra credit.
        jobs: list[tuple[str, dict[str, Any], str, str]] = []
        jobs.extend(
            ("advisory", dependency, self._advisory_query(dependency), "advanced")
            for dependency in advisory_targets
        )
        jobs.extend(
            ("version", dependency, self._version_query(dependency), "basic")
            for dependency in version_targets
        )

        for kind, dependency, query, response in self._run(jobs):
            if response is None:
                report["errors"].append(f"{dependency['name']}: {kind} search failed")
                report["unverified"].append({**self._dependency_summary(dependency), "reason": f"{kind} search failed"})
                continue

            report["queries_run"] += 1
            if kind == "advisory":
                self._apply_advisory(report, dependency, query, response)
            else:
                self._apply_version(report, dependency, query, response)

        for dependency in candidates[advisory_budget:]:
            report["unverified"].append(
                {**self._dependency_summary(dependency), "reason": "not checked: per-run search budget reached"}
            )

        report["not_checked"] = len(report["unverified"])
        return report

    def _dependency_candidates(self, manifests: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Directly declared dependencies first, then resolved lockfile entries.

        A package.json is a curated list of what the project chose; a lockfile is
        a machine dump of everything that arrived with it. When both name the same
        package the declared version wins, because that is the one the project
        actually pinned.
        """
        candidates: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[str, str]] = []

        for manifest in manifests:
            ecosystem = str(manifest.get("ecosystem", "unknown"))
            resolved = bool(manifest.get("resolved"))
            for dependency in manifest.get("dependencies", []):
                name = str(dependency.get("name", "")).strip()
                if not name or name.lower() == "python":
                    continue
                version = str(dependency.get("version", "")).strip()
                key = (ecosystem, name.lower())
                existing = candidates.get(key)

                if existing is None:
                    candidates[key] = {
                        "name": name,
                        "version": version,
                        "ecosystem": ecosystem,
                        "declared_in": str(manifest.get("file_path", "")),
                        "resolved": resolved,
                    }
                    order.append(key)
                    continue

                # A lockfile pins exactly; a manifest may only give a range.
                if _pinned_version(version) and not _pinned_version(str(existing.get("version", ""))):
                    existing["version"] = version
                # A curated manifest is the better place to cite the dependency.
                if not resolved and existing.get("resolved"):
                    existing["declared_in"] = str(manifest.get("file_path", ""))
                    existing["resolved"] = False

        return [candidates[key] for key in order]

    def _apply_advisory(self, report: dict[str, Any], dependency: dict[str, Any], query: str, response: TavilyResponse) -> None:
        """Record an advisory only when the text names the package and a CVE."""
        text = response.text
        mentions_package = dependency["name"].lower() in text.lower()
        cves = sorted({match.upper() for match in _CVE_RE.findall(text)})
        pinned = _pinned_version(str(dependency.get("version", "")))

        if not cves or not mentions_package:
            report["checked"].append(
                {
                    **self._dependency_summary(dependency),
                    "advisories_found": len(cves),
                    "query": query,
                    "sources": response.top_sources(2),
                }
            )
            return

        fixed_in = _FIXED_IN_RE.search(text)
        report["vulnerable"].append(
            {
                **self._dependency_summary(dependency),
                "cves": cves[:5],
                "severity": _severity_from_text(response.answer) or _severity_from_text(text),
                "summary": _evidence_line(text, response.answer),
                "fixed_in": f">= {fixed_in.group(1)}" if fixed_in else "",
                "pinned_version": pinned,
                "confidence": "needs-confirmation",
                "query": query,
                "sources": response.top_sources(4),
            }
        )

    def _apply_version(self, report: dict[str, Any], dependency: dict[str, Any], query: str, response: TavilyResponse) -> None:
        """Compare the pinned version with the version named in the answer."""
        pinned_text = _pinned_version(str(dependency.get("version", "")))
        pinned = _parse_version(pinned_text)
        latest = _latest_version(response.answer or " ".join(source.snippet for source in response.sources[:2]))

        if not pinned or not latest:
            report["unverified"].append(
                {
                    **self._dependency_summary(dependency),
                    "reason": "no parsable version in the search answer",
                    "query": query,
                    "sources": response.top_sources(2),
                }
            )
            return

        if latest > pinned:
            report["outdated"].append(
                {
                    **self._dependency_summary(dependency),
                    "latest_version": _format_version(latest),
                    "behind_by": _distance_behind(pinned, latest),
                    "confidence": "needs-confirmation",
                    "query": query,
                    "sources": response.top_sources(2),
                }
            )
        else:
            report["checked"].append(
                {
                    **self._dependency_summary(dependency),
                    "latest_version": _format_version(latest),
                    "query": query,
                }
            )

    @staticmethod
    def _dependency_summary(dependency: dict[str, Any]) -> dict[str, Any]:
        spec = str(dependency.get("version", ""))
        return {
            "name": dependency["name"],
            "version": _pinned_version(spec) or spec[:40],
            "declared_spec": spec[:40],
            "ecosystem": dependency.get("ecosystem", "unknown"),
            "ecosystem_label": ECOSYSTEM_LABELS.get(str(dependency.get("ecosystem", "")), "registry"),
            "declared_in": dependency.get("declared_in", ""),
        }

    def _advisory_query(self, dependency: dict[str, Any]) -> str:
        version = _pinned_version(str(dependency.get("version", "")))
        label = ECOSYSTEM_LABELS.get(str(dependency.get("ecosystem", "")), "package registry")
        return (
            f"{dependency['name']} {version} {label} security advisory CVE known vulnerabilities"
            if version
            else f"{dependency['name']} {label} security advisory CVE known vulnerabilities"
        )

    def _version_query(self, dependency: dict[str, Any]) -> str:
        label = ECOSYSTEM_LABELS.get(str(dependency.get("ecosystem", "")), "package registry")
        return f"latest stable released version of the {dependency['name']} package on {label} 2026"

    # -- findings ---------------------------------------------------------- #

    def ground_findings(self, findings: Sequence[dict[str, Any]], limit: int | None = None) -> dict[str, dict[str, Any]]:
        """Attach authoritative guidance to the most severe findings.

        Returns a mapping of finding id to ``{"query", "answer", "sources"}`` so
        the caller can attach it to a finding without another search.
        """
        budget = int(os.getenv("CODELENS_TAVILY_FINDING_QUERIES", "3")) if limit is None else limit
        if not self.available or budget <= 0:
            return {}

        targets = [
            finding
            for finding in findings
            if str(finding.get("rule", "")) in GROUNDING_QUERIES or str(finding.get("triage", "")) in {"confirmed", "escalated"}
        ][:budget]
        if not targets:
            return {}

        jobs = [
            (
                str(finding.get("id", "")),
                {},
                GROUNDING_QUERIES.get(
                    str(finding.get("rule", "")),
                    f"{finding.get('name', 'vulnerability')} remediation OWASP",
                ),
                "basic",
            )
            for finding in targets
        ]

        grounded: dict[str, dict[str, Any]] = {}
        for finding_id, _, query, response in self._run(jobs, include_domains=GUIDANCE_DOMAINS):
            if not finding_id or response is None:
                continue
            if not response.sources:
                # A restricted search that finds nothing is retried unrestricted.
                retry = self._safe_search(query, None, "basic")
                if retry is None:
                    continue
                response = retry
            grounded[finding_id] = {
                "query": query,
                "answer": response.answer[:600],
                "sources": response.top_sources(3),
            }
        return grounded

    # -- shared ------------------------------------------------------------ #

    def _run(
        self,
        jobs: Sequence[tuple[str, dict[str, Any], str, str]],
        include_domains: Sequence[str] | None = None,
    ) -> list[tuple[str, dict[str, Any], str, TavilyResponse | None]]:
        """Run independent searches with a bounded pool; a failure is recorded, not raised."""
        if not jobs:
            return []

        answers: list[TavilyResponse | None] = [None] * len(jobs)

        def run(query: str, depth: str) -> TavilyResponse | None:
            return self._safe_search(query, include_domains, depth)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.max_concurrency, len(jobs))) as pool:
            futures = {pool.submit(run, query, depth): index for index, (_, _, query, depth) in enumerate(jobs)}
            for future in concurrent.futures.as_completed(futures):
                index = futures[future]
                try:
                    answers[index] = future.result()
                except Exception as error:  # noqa: BLE001 - a failed search must not fail the run
                    print(f"Tavily search failed: {error}", flush=True)

        return [
            (job_id, payload, query, answers[index])
            for index, (job_id, payload, query, _) in enumerate(jobs)
        ]

    def _safe_search(
        self,
        query: str,
        include_domains: Sequence[str] | None,
        search_depth: str = "advanced",
    ) -> TavilyResponse | None:
        try:
            return self.client.search(query, include_domains=include_domains, search_depth=search_depth)
        except TavilyError as error:
            print(f"Tavily search failed for {query!r}: {error}", flush=True)
            return None


# --------------------------------------------------------------------------- #
# version handling
# --------------------------------------------------------------------------- #

def _pinned_version(spec: str) -> str:
    """Reduce a requirement specifier to the version it pins, if it pins one."""
    cleaned = (spec or "").strip()
    for prefix in ("==", "~=", ">=", "<=", ">", "<", "=", "^", "@"):
        cleaned = cleaned.lstrip(prefix).strip()
    cleaned = cleaned.split(";")[0].split(",")[0].split(" ")[0].strip().strip("[]()")
    return cleaned if re.fullmatch(r"v?\d+(?:\.\d+)*[A-Za-z0-9.\-+]*", cleaned) else ""


def _parse_version(value: str) -> tuple[int, ...] | None:
    """Numeric parts of a version, ignoring any pre-release suffix."""
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", value or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _format_version(parts: Sequence[int]) -> str:
    return ".".join(str(part) for part in parts)


def _latest_version(text: str) -> tuple[int, ...] | None:
    """The version a search answer presents as current.

    Answers usually read "the latest version of flask is 3.1.0", but sometimes
    "flask 3.1.0 is the current release", so both orders are tried. Only the
    first number after such a phrase is taken, so a list of historical releases
    in the same answer cannot be mistaken for the current one; a bare version
    anywhere in the text is the last resort.
    """
    if not text:
        return None

    for pattern in (
        r"(?:latest|current|newest|most recent)[^.\n]{0,40}?(\d+(?:\.\d+)+)",
        r"(\d+(?:\.\d+)+)[^.\n]{0,24}?(?:is|as)\s+the\s+(?:latest|current|newest)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            parsed = _parse_version(match.group(1))
            if parsed:
                return parsed
    return _parse_version(text)


def _distance_behind(pinned: tuple[int, ...], latest: tuple[int, ...]) -> str:
    """How far behind a version is, in a form a human can read at a glance."""
    length = max(len(pinned), len(latest))
    padded_pinned = pinned + (0,) * (length - len(pinned))
    padded_latest = latest + (0,) * (length - len(latest))
    if padded_pinned[0] != padded_latest[0]:
        return f"{padded_latest[0] - padded_pinned[0]} major release(s) behind"
    if length > 1 and padded_pinned[1] != padded_latest[1]:
        return f"{padded_latest[1] - padded_pinned[1]} minor release(s) behind"
    return f"{max(0, padded_latest[-1] - padded_pinned[-1])} patch release(s) behind"


def _severity_from_text(text: str) -> str:
    lowered = text.lower()
    for severity, keywords in SEVERITY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return severity
    return "UNKNOWN"


def _evidence_line(text: str, answer: str) -> str:
    """The most relevant sentence in the answer, quoted rather than paraphrased."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", answer or text) if part.strip()]
    if not sentences:
        return ""
    for sentence in sentences:
        if _SNIPPET_RE.match(sentence) or _CVE_RE.search(sentence):
            return sentence[:300]
    return sentences[0][:300]


def _source_from(item: dict[str, Any]) -> Source:
    return Source(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or "")[:1200],
        score=float(item.get("score") or 0.0),
    )


def _retry_after(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After", "")
    try:
        return max(0.5, min(float(header), 30.0))
    except (TypeError, ValueError):
        return 1.5 * (attempt + 1)


def _safe_error(response: httpx.Response) -> str:
    """Provider errors, without echoing anything that could be a key."""
    try:
        body = response.json()
        detail = body.get("detail") or body.get("error") or ""
        if isinstance(detail, dict):
            detail = detail.get("message", "")
        return str(detail)[:200]
    except Exception:  # noqa: BLE001 - error bodies are not always JSON
        return response.text[:200]
