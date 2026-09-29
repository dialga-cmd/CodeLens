"""Model-backed repository analysis, built on the shared Token Factory client.

This layer is deliberately thin: it prepares a code summary, asks the heavy model
for structured findings, and normalises whatever shape comes back. All transport
concerns (retries, timeouts, JSON repair, key handling) live in
:mod:`app.core.llm`.

Everything here degrades instead of failing. If no API key is configured, or the
provider is unreachable, the deterministic analysis performed by
:class:`app.core.analyzer.CodeAnalyzer` still completes and the run is labelled
as unverified.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Sequence

from .llm import ROLE_HEAVY, LLMError, get_llm_client
from .prompts import load_prompt

_MAX_SUMMARY_FILES = 50
_MAX_SUMMARY_DETAIL = 20
_MAX_SNIPPET_LINES = 20
_MAX_SNIPPET_CHARS = 1200

_EMPTY_RESULT: dict[str, Any] = {
    "vulnerabilities": [],
    "hotspots": [],
    "tech_stack": {},
    "dependencies": {},
    "recommendations": "",
}


class AIAnalyzer:
    """Asks the heavy model for findings about an already-parsed repository."""

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
            except Exception:  # noqa: BLE001 - progress must never break a run
                pass

    # -- public API -------------------------------------------------------- #

    def analyze_codebase(self, files: Sequence[dict[str, Any]], repo_info: str = "") -> dict[str, Any]:
        """Return vulnerabilities, hotspots, tech stack and dependency insights."""
        if not self.available:
            self._report("No NEBIUS_API_KEY configured - skipping model analysis, keeping deterministic results only.")
            return {**_EMPTY_RESULT, "llm_usage": []}

        try:
            code_summary = self._prepare_code_summary(files)
            if not code_summary:
                self._report("No readable source files to summarise - skipping model analysis.")
                return {**_EMPTY_RESULT, "llm_usage": self.usage_log}

            self._report(f"Asking {self.model_id} for security, dependency and quality findings...")
            scan = self._run_comprehensive_scan(code_summary, repo_info)

            self._report("Asking for a second opinion on the most complex areas...")
            hotspots = self._identify_hotspots(files, code_summary)

            return {
                "vulnerabilities": self._extract_vulnerabilities(scan),
                "hotspots": hotspots,
                "tech_stack": scan.get("tech_stack", {}) if isinstance(scan, dict) else {},
                "dependencies": scan.get("dependencies", {}) if isinstance(scan, dict) else {},
                "recommendations": str(scan.get("summary", "")) if isinstance(scan, dict) else "",
                "llm_usage": self.usage_log,
            }
        except LLMError as error:
            self._report(f"Model analysis unavailable: {error}. Continuing with deterministic results only.")
            return {**_EMPTY_RESULT, "llm_usage": self.usage_log}
        except Exception as error:  # noqa: BLE001 - a model pass must not fail the run
            self._report(f"Unexpected error during model analysis: {error}. Continuing with deterministic results only.")
            return {**_EMPTY_RESULT, "llm_usage": self.usage_log}

    # -- prompts ----------------------------------------------------------- #

    def _prepare_code_summary(self, files: Sequence[dict[str, Any]]) -> str:
        """Compact, deterministic description of the repository for the model."""
        parts: list[str] = []
        detailed = 0

        for file in files[:_MAX_SUMMARY_FILES]:
            if detailed >= _MAX_SUMMARY_DETAIL:
                break

            file_path = str(file.get("file_path", ""))
            if not file_path:
                continue

            content = str(file.get("content", ""))
            preview = "\n".join(content.splitlines()[:_MAX_SNIPPET_LINES])
            function_names = [
                str(item.get("name", "unknown")) if isinstance(item, dict) else str(item)
                for item in list(file.get("functions", []))[:5]
            ]

            parts.append(
                "\n".join(
                    [
                        f"File: {file_path} ({file.get('language', 'unknown')})",
                        f"Lines: {len(content.splitlines())}",
                        f"Cyclomatic complexity: {file.get('complexity', 0)}",
                        f"Functions: {', '.join(function_names) if function_names else 'n/a'}",
                        f"Imports: {', '.join(str(item) for item in list(file.get('imports', []))[:12]) or 'none'}",
                        "Snippet:",
                        preview[:_MAX_SNIPPET_CHARS],
                    ]
                )
            )
            detailed += 1

        return "\n\n".join(parts)

    def _ask_json(self, instruction: str, context: str, temperature: float = 0.2) -> dict[str, Any]:
        """Send a prompt built from the analysis template and parse the JSON answer."""
        template = load_prompt("analysis_prompt.txt")
        user_prompt = f"{template}\n\n{instruction}\n\nRepository evidence:\n{context}"
        data, result = self.client.chat_json_sync(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a senior application-security reviewer and software architect. "
                        "You only report what the provided evidence supports."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            role=self.role,
            model=self.model,
            temperature=temperature,
        )
        usage = result.usage_dict()
        usage["step"] = instruction.split(".")[0][:80]
        self.usage_log.append(usage)
        return data if isinstance(data, dict) else {"items": data}

    def _run_comprehensive_scan(self, code_summary: str, repo_info: str = "") -> dict[str, Any]:
        instruction = (
            "Identify the tech stack, the notable dependencies, the security risks and the code hotspots "
            "visible in this repository evidence."
        )
        if repo_info:
            instruction = f"{instruction}\nRepository: {repo_info}"
        return self._ask_json(instruction, code_summary, temperature=0.15)

    def _identify_hotspots(self, files: Sequence[dict[str, Any]], code_summary: str) -> list[dict[str, Any]]:
        ranked = sorted(
            (file for file in files if str(file.get("file_path", ""))),
            key=lambda file: int(file.get("complexity", 0) or 0),
            reverse=True,
        )[:30]
        inventory = json.dumps(
            [
                {
                    "file_path": file.get("file_path"),
                    "language": file.get("language"),
                    "complexity": file.get("complexity", 0),
                    "lines": len(str(file.get("content", "")).splitlines()),
                }
                for file in ranked
            ],
            indent=2,
        )
        try:
            data = self._ask_json(
                "Focus on the hotspots listed here: explain why the most complex files are risky to change and "
                "what refactoring would pay off.",
                f"Complexity inventory:\n{inventory}\n\nCode evidence:\n{code_summary}",
            )
        except LLMError as error:
            self._report(f"Hotspot pass unavailable: {error}")
            return []
        return self._extract_hotspots(data)

    # -- normalisation of loosely shaped model output --------------------- #

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            match = re.search(r"(\d+)", str(value))
            return int(match.group(1)) if match else default

    def _extract_vulnerabilities(self, parsed: Any) -> list[dict[str, Any]]:
        """Pull findings out of any of the shapes a model tends to produce."""
        items: list[Any] = []

        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            for key in ("security_risks", "vulnerabilities", "vulnerable", "findings"):
                candidate = parsed.get(key)
                if isinstance(candidate, list) and candidate:
                    items = candidate
                    break
            else:
                dependencies = parsed.get("dependencies")
                if isinstance(dependencies, dict):
                    items = list(dependencies.get("vulnerable", []) or [])

        findings: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            location = str(item.get("location") or item.get("file_path") or "")
            file_path, _, line_part = location.partition(":")
            findings.append(
                {
                    "name": str(item.get("name") or item.get("type") or "Unspecified issue"),
                    "severity": str(item.get("severity", "MEDIUM")).upper(),
                    "description": str(item.get("description", "")),
                    "file_path": file_path or str(item.get("file_path", "")),
                    "line": self._safe_int(line_part or item.get("line", 0)),
                    "snippet": str(item.get("snippet", ""))[:200],
                    "recommendation": str(item.get("recommendation", "")),
                    "source": "model",
                }
            )
        return findings

    def _extract_hotspots(self, parsed: Any) -> list[dict[str, Any]]:
        items: list[Any] = []
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            for key in ("hotspots", "code_hotspots", "complexity_hotspots", "items"):
                candidate = parsed.get(key)
                if isinstance(candidate, list) and candidate:
                    items = candidate
                    break

        hotspots: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            hotspots.append(
                {
                    "file_path": str(item.get("file_path", "")),
                    "complexity": self._safe_int(item.get("complexity") or item.get("complexity_score", 0)),
                    "language": str(item.get("language", "unknown")),
                    "reason": str(item.get("reason", "")),
                    "suggestions": [str(entry) for entry in list(item.get("suggestions", []) or [])],
                }
            )
        return [hotspot for hotspot in hotspots if hotspot["file_path"]]
