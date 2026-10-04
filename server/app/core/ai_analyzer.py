"""Model passes over an already-measured repository.

By the time this module runs, the deterministic stages are done: files are
parsed, the import graph is resolved, git history is read and the static
scanner has produced a short list of located candidates. The model is used for
the three things it is actually good at here:

* triaging candidates it can see the evidence for, including dismissing the
  false positives a regex cannot rule out;
* describing the architecture from the measurements;
* writing remediation advice that names the code to change.

Every method returns something usable when the provider is unavailable, when a
chunk fails, or when the answer is unusable, and every call records its model,
token counts and latency.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
from typing import Any, Callable, Sequence

from .llm import ROLE_HEAVY, LLMError, get_llm_client
from .prompts import load_prompt

# How many candidates to review per request. Small enough that the evidence
# stays legible, large enough that a repository does not need thirty calls.
TRIAGE_CHUNK_SIZE = 12
FIX_CHUNK_SIZE = 8

# Findings that get model-written remediation. Beyond this the scanner's own
# recommendation stands, and the report says so.
MAX_FIX_TARGETS = 24

SYSTEM_TRIAGE = (
    "You are a senior application-security reviewer. You are given located findings from a static "
    "scanner together with the source lines that triggered them. Judge each one on its evidence and "
    "be willing to dismiss it. You answer in JSON only."
)
SYSTEM_ARCHITECTURE = (
    "You are a software architect documenting a codebase you have measured. You describe what the "
    "measurements show, in plain language, and never invent structure that is not in the evidence. "
    "You answer in JSON only."
)
SYSTEM_FIXES = (
    "You are a senior engineer writing remediation advice for specific code. You name the call site "
    "and the change. You answer in JSON only."
)

SYSTEM = {
    "triage": SYSTEM_TRIAGE,
    "architecture": SYSTEM_ARCHITECTURE,
    "fixes": SYSTEM_FIXES,
}

VERDICTS = {"confirm", "downgrade", "dismiss", "escalate"}

_VERDICT_TO_TRIAGE = {
    "confirm": "confirmed",
    "downgrade": "downgraded",
    "dismiss": "dismissed",
    "escalate": "escalated",
}


class AIAnalyzer:
    """Model-backed passes over a repository that has already been measured."""

    def __init__(self, role: str = ROLE_HEAVY, model: str | None = None) -> None:
        self.client = get_llm_client()
        self.role = role
        self.model = model
        self.usage_log: list[dict[str, Any]] = []
        self._progress_callback: Callable[[str], None] | None = None

    # -- availability ------------------------------------------------------ #

    @property
    def available(self) -> bool:
        return self.client.configured

    @property
    def model_id(self) -> str:
        return self.model or self.client.model_for(self.role)

    # -- progress ---------------------------------------------------------- #

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self._progress_callback = callback

    def _report(self, message: str) -> None:
        print(message, flush=True)
        if self._progress_callback:
            try:
                self._progress_callback(message)
            except Exception as error:  # noqa: BLE001 - progress must never break a run
                print(f"progress callback failed: {error}")

    # -- triage ------------------------------------------------------------ #

    def triage_findings(
        self,
        findings: Sequence[dict[str, Any]],
        repo_info: str = "",
        context_files: Sequence[dict[str, Any]] = (),
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Review located candidates.

        Returns ``(triaged_findings, model_only_findings, usage)``. Findings the
        model dismissed are kept with ``triage="dismissed"`` rather than deleted,
        because a dismissal is itself something a reviewer should be able to see
        and overrule.
        """
        if not self.available:
            self._report("No NEBIUS_API_KEY configured - keeping static analysis results unreviewed.")
            return [self._mark_unreviewed(finding) for finding in findings], [], []

        before = len(self.usage_log)
        triaged = [self._mark_unreviewed(finding) for finding in findings]
        if not triaged:
            return triaged, [], []

        contents = {
            str(file.get("file_path", "")): str(file.get("content", ""))
            for file in context_files
            if file.get("file_path")
        }
        chunks = [
            [self._candidate_payload(finding, contents) for finding in triaged[index : index + TRIAGE_CHUNK_SIZE]]
            for index in range(0, len(triaged), TRIAGE_CHUNK_SIZE)
        ]

        self._report(
            f"Reviewing {len(triaged)} candidates in {len(chunks)} batches with {self.model_id}..."
        )
        results = self._run_chunks(chunks, lambda batch: self._triage_chunk(batch, repo_info))

        model_only: list[dict[str, Any]] = []
        for payload, data in results:
            if data is None:
                continue
            verdicts = data.get("verdicts") if isinstance(data, dict) else None
            by_id = {
                str(verdict.get("id", "")): verdict
                for verdict in (verdicts or [])
                if isinstance(verdict, dict) and verdict.get("id")
            }
            for candidate in payload:
                finding = self._find_by_id(triaged, str(candidate["id"]))
                if finding is None:
                    continue
                verdict = by_id.get(str(candidate["id"]))
                if verdict is None:
                    continue
                self._apply_verdict(finding, verdict)

            for extra in (data.get("additional_findings") if isinstance(data, dict) else None) or []:
                parsed = self._parse_model_finding(extra, repo_info)
                if parsed:
                    model_only.append(parsed)

        dismissed = sum(1 for finding in triaged if finding.get("triage") == "dismissed")
        confirmed = sum(1 for finding in triaged if finding.get("triage") == "confirmed")
        self._report(
            f"Model triage: {confirmed} confirmed, {dismissed} dismissed, {len(model_only)} further issues reported."
        )
        return triaged, model_only, list(self.usage_log[before:])

    def _triage_chunk(self, candidates: list[dict[str, Any]], repo_info: str = "") -> dict[str, Any]:
        template = load_prompt("triage_prompt.txt")
        evidence = json.dumps(candidates, indent=1)
        instruction = (
            "Review these candidates. Return one verdict for every candidate id, and nothing else unless "
            "you can see a real issue in the evidence shown."
        )
        data, _ = self._ask_json(
            "triage",
            f"{template}\n\n{instruction}\n\nRepository: {repo_info or 'unknown'}\n\nCandidates:\n{evidence}",
            max_output_tokens=12000,
        )
        return data if isinstance(data, dict) else {}

    def _candidate_payload(self, finding: dict[str, Any], contents: dict[str, str]) -> dict[str, Any]:
        """One candidate plus the lines around it, so the model can judge context."""
        file_path = str(finding.get("file_path", ""))
        line = int(finding.get("line", 0) or 0)
        payload = {
            "id": finding.get("id"),
            "rule": finding.get("rule") or finding.get("id"),
            "detector": finding.get("detector", "pattern"),
            "reported_severity": finding.get("severity", "MEDIUM"),
            "file_path": file_path,
            "line": line,
            "description": finding.get("description", ""),
            "matched_line": str(finding.get("snippet", ""))[:300],
        }

        surrounding = self._surrounding_lines(contents.get(file_path, ""), line, radius=4)
        if surrounding:
            payload["surrounding_code"] = surrounding
        return payload

    @staticmethod
    def _surrounding_lines(content: str, line: int, radius: int = 4) -> str:
        if not content or line <= 0:
            return ""
        lines = content.splitlines()
        start = max(0, line - 1 - radius)
        end = min(len(lines), line + radius)
        return "\n".join(
            f"{number}: {text}" for number, text in enumerate(lines[start:end], start=start + 1)
        )

    def _apply_verdict(self, finding: dict[str, Any], verdict: dict[str, Any]) -> None:
        raw = str(verdict.get("verdict", "")).strip().lower()
        if raw not in VERDICTS:
            return

        finding["triage"] = _VERDICT_TO_TRIAGE[raw]
        finding["triage_note"] = str(verdict.get("explanation", ""))[:800]

        severity = str(verdict.get("severity", "")).strip().upper()
        # A dismissal keeps the severity the scanner gave it: there is no risk to
        # rate once the match is judged safe.
        if severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"} and raw != "dismiss":
            if severity != str(finding.get("severity", "")).upper():
                finding["original_severity"] = finding.get("severity")
                finding["severity"] = severity

        recommendation = str(verdict.get("recommendation", "")).strip()
        if recommendation:
            finding["recommendation"] = recommendation[:1200]

    def _parse_model_finding(self, payload: Any, repo_info: str) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        file_path = str(payload.get("file_path", "")).strip()
        description = str(payload.get("description", "")).strip()
        if not file_path or not description:
            return None

        severity = str(payload.get("severity", "MEDIUM")).strip().upper()
        if severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            severity = "MEDIUM"
        try:
            line = int(payload.get("line", 0) or 0)
        except (TypeError, ValueError):
            line = 0

        name = str(payload.get("name", "Model-reported issue")).strip()[:120] or "Model-reported issue"
        return {
            "id": f"model:{name.lower().replace(' ', '-')[:40]}:{file_path}:{line}",
            "rule": "model_review",
            "name": name,
            "severity": severity,
            "description": description[:1200],
            "file_path": file_path,
            "line": line,
            "snippet": str(payload.get("snippet", ""))[:200],
            "recommendation": str(payload.get("recommendation", ""))[:1200],
            "detector": "model",
            "confidence": "low",
            "source": "model-review",
            "triage": "unverified",
            "triage_note": "Reported during model review of the scanner's evidence. Not reproduced by static analysis.",
        }

    # -- architecture ------------------------------------------------------ #

    def summarise_architecture(
        self,
        *,
        tech_stack: dict[str, Any] | None = None,
        manifests: Sequence[dict[str, Any]] = (),
        entry_points: Sequence[str] = (),
        graph_metrics: dict[str, Any] | None = None,
        hotspots: Sequence[dict[str, Any]] = (),
        repo_info: str = "",
        readme: str = "",
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Describe how the measured repository fits together."""
        if not self.available:
            return {}, []

        before = len(self.usage_log)
        facts: dict[str, Any] = {
            "repository": repo_info or "unknown",
            "tech_stack": tech_stack or {},
            "graph": graph_metrics or {},
            "entry_points": list(entry_points)[:12],
            "hotspots": [
                {
                    "file_path": hotspot.get("file_path"),
                    "language": hotspot.get("language"),
                    "score": hotspot.get("score"),
                    "complexity": hotspot.get("complexity"),
                    "fan_in": hotspot.get("fan_in"),
                    "fan_out": hotspot.get("fan_out"),
                    "loc": hotspot.get("loc"),
                    "reasons": hotspot.get("reasons", [])[:3],
                }
                for hotspot in list(hotspots)[:10]
            ],
            "dependencies": self._dependency_facts(manifests),
        }
        if readme:
            facts["readme_excerpt"] = readme[:3000]

        try:
            data, _ = self._ask_json(
                "architecture",
                f"{load_prompt('architecture_prompt.txt')}\n\nMeasurements:\n{json.dumps(facts, indent=1)[:20000]}",
                max_output_tokens=8000,
            )
        except LLMError as error:
            self._report(f"Architecture summary unavailable: {error}")
            return {}, list(self.usage_log[before:])

        cleaned = self._clean_architecture(data if isinstance(data, dict) else {}, self.model_id)
        return cleaned, list(self.usage_log[before:])

    def _dependency_facts(self, manifests: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """The dependency inventory, capped so the prompt stays readable."""
        facts: list[dict[str, Any]] = []
        for manifest in manifests:
            dependencies = [
                {
                    "name": str(dependency.get("name", "")),
                    "version": str(dependency.get("version", ""))[:32],
                }
                for dependency in manifest.get("dependencies", [])
                if dependency.get("name")
            ]
            if not dependencies:
                continue
            facts.append(
                {
                    "file": str(manifest.get("file_path", "")),
                    "ecosystem": manifest.get("ecosystem", ""),
                    "count": len(dependencies),
                    "declared": dependencies[:25],
                    "scripts": manifest.get("scripts", [])[:12],
                }
            )
        return facts[:8]

    @staticmethod
    def _clean_architecture(data: dict[str, Any], model: str = "") -> dict[str, Any]:
        def _strings(key: str, limit: int) -> list[str]:
            value = data.get(key)
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()][:limit]
            return []

        def _objects(key: str, fields: Sequence[str], limit: int) -> list[dict[str, Any]]:
            value = data.get(key)
            if not isinstance(value, list):
                return []
            cleaned: list[dict[str, Any]] = []
            for item in value[:limit]:
                if not isinstance(item, dict):
                    continue
                entry = {field: item[field] for field in fields if field in item}
                if entry:
                    cleaned.append(entry)
            return cleaned

        return {
            "summary": str(data.get("summary", ""))[:2000],
            "pattern": str(data.get("pattern", ""))[:400],
            "layers": _objects("layers", ("name", "files", "responsibility"), 8),
            "entry_points": _objects("entry_points", ("file_path", "role", "calls"), 10),
            "data_flow": _strings("data_flow", 10),
            "extension_points": _objects("extension_points", ("file_path", "how"), 8),
            "risks": _objects("risks", ("file_path", "risk"), 8),
            "generated_by": model,
        }

    # -- remediation ------------------------------------------------------- #

    def recommend_fixes(
        self,
        findings: Sequence[dict[str, Any]],
        repo_info: str = "",
        context_files: Sequence[dict[str, Any]] = (),
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Write remediation advice for the findings that survived triage."""
        if not self.available:
            return {}, []

        before = len(self.usage_log)
        targets = [
            finding
            for finding in findings
            if finding.get("triage") in {"confirmed", "escalated", "unverified", "unreviewed", "downgraded"}
        ][:MAX_FIX_TARGETS]
        if not targets:
            return {}, []

        contents = {
            str(file.get("file_path", "")): str(file.get("content", ""))
            for file in context_files
            if file.get("file_path")
        }
        payload = [
            {
                "id": finding.get("id"),
                "name": finding.get("name"),
                "severity": finding.get("severity"),
                "file_path": finding.get("file_path"),
                "line": finding.get("line"),
                "matched_line": str(finding.get("snippet", ""))[:200],
                "scanner_recommendation": str(finding.get("recommendation", ""))[:200],
                "surrounding_code": self._surrounding_lines(
                    contents.get(str(finding.get("file_path", "")), ""),
                    int(finding.get("line", 0) or 0),
                    radius=3,
                ),
            }
            for finding in targets
        ]

        chunks = [payload[index : index + FIX_CHUNK_SIZE] for index in range(0, len(payload), FIX_CHUNK_SIZE)]
        self._report(f"Writing remediation for {len(payload)} findings with {self.model_id}...")
        results = self._run_chunks(chunks, self._fix_chunk)

        fixes: dict[str, dict[str, Any]] = {}
        for chunk, data in results:
            if not isinstance(data, dict):
                continue
            for fix in data.get("fixes", []) or []:
                if not isinstance(fix, dict) or not fix.get("id"):
                    continue
                recommendation = str(fix.get("recommendation", "")).strip()
                if not recommendation:
                    continue
                fixes[str(fix["id"])] = {
                    "recommendation": recommendation[:1500],
                    "effort": str(fix.get("effort", "")).strip().lower(),
                    "breaking_change": bool(fix.get("breaking_change", False)),
                }

        return fixes, list(self.usage_log[before:])

    def _fix_chunk(self, findings: list[dict[str, Any]]) -> dict[str, Any]:
        data, _ = self._ask_json(
            "fixes",
            f"{load_prompt('fix_prompt.txt')}\n\nFindings:\n{json.dumps(findings, indent=1)}",
            max_output_tokens=6000,
        )
        return data if isinstance(data, dict) else {}

    # -- shared plumbing --------------------------------------------------- #

    def _run_chunks(
        self,
        chunks: Sequence[Sequence[Any]],
        worker: Callable[[list[Any]], dict[str, Any]],
    ) -> list[tuple[list[Any], dict[str, Any] | None]]:
        """Run independent batches with a bounded pool; a failed batch is not fatal.

        Results come back in the order the batches were given, so a caller can
        line a response up with the evidence that produced it.
        """
        if not chunks:
            return []

        answers: list[dict[str, Any] | None] = [None] * len(chunks)
        max_workers = max(1, min(len(chunks), self.client.max_concurrency))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(worker, list(chunk)): index for index, chunk in enumerate(chunks)}
            for future in concurrent.futures.as_completed(futures):
                index = futures[future]
                try:
                    answers[index] = future.result()
                except Exception as error:  # noqa: BLE001 - one bad batch must not lose the run
                    self._report(f"A batch could not be completed and was skipped: {error}")

        return [(list(chunk), answers[index]) for index, chunk in enumerate(chunks)]

    def _ask_json(
        self,
        step: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_output_tokens: int | None = None,
    ) -> tuple[Any, Any]:
        """One structured call, recorded in the usage log with the step it served."""
        data, result = self.client.chat_json_sync(
            [
                {"role": "system", "content": SYSTEM.get(step, "You answer in JSON only.")},
                {"role": "user", "content": user_prompt},
            ],
            role=self.role,
            model=self.model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        usage = result.usage_dict()
        usage["step"] = step
        # finish_reason="length" means the reasoning model spent the whole budget
        # thinking and never reached its JSON. Recording it keeps a truncated
        # answer from being read as a considered one.
        usage["finish_reason"] = result.finish_reason
        self.usage_log.append(usage)
        return data, result

    @staticmethod
    def _mark_unreviewed(finding: dict[str, Any]) -> dict[str, Any]:
        marked = dict(finding)
        marked.setdefault("triage", "unreviewed")
        return marked

    @staticmethod
    def _find_by_id(findings: Sequence[dict[str, Any]], finding_id: str) -> dict[str, Any] | None:
        for finding in findings:
            if str(finding.get("id", "")) == finding_id:
                return finding
        return None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            match = re.search(r"(\d+)", str(value))
            return int(match.group(1)) if match else default
