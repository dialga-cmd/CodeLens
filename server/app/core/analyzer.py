"""The analysis pipeline.

The order matters, and it is the whole point of the design:

1. parse every file with Tree-sitter and collect real metrics;
2. resolve the import graph;
3. read git history for churn;
4. run the deterministic security pre-scan;
5. rank hotspots from those signals alone;
6. only then ask a model to triage the pre-scan candidates, summarise the
   architecture and comment on the hotspots.

Hotspots and findings are therefore reproducible without any model access: a
missing key downgrades the report, it does not empty it.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Iterable, Sequence

from .ai_analyzer import AIAnalyzer
from .git_metrics import ChurnReport, GitHistory
from .graph import GraphBuilder, DependencyGraph
from .hotspots import rank_hotspots
from .ingestion import IngestionEngine, normalize_repo_url
from .parser import (
    extract_imports_with_regex,
    is_generated_path,
    language_for_path,
    parse_source_safely,
)
from .research import ResearchEngine
from .security import SecurityScanner, rank_findings, summarize as summarize_findings

try:
    import tomllib
except Exception:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

# Findings the model reports that static analysis could not have found. They are
# always labelled, because an unreproduced claim should not read like a scan hit.
MODEL_FINDING_SOURCE = "model-review"


class CodeAnalyzer:
    SKIP_DIRS = {
        ".git", "node_modules", "__pycache__", ".next", "dist", "build", "coverage",
        "venv", ".venv", "vendor", "target", "site-packages", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".gradle", "out", ".idea", ".vscode",
    }
    MAX_FILES = 250
    MAX_FILE_BYTES = 200_000
    # Lockfiles are large and machine-generated, but they are the only place
    # exact dependency versions are written down, so they get their own budget.
    # They are parsed locally and never sent to a model verbatim.
    MAX_MANIFEST_BYTES = 2_000_000
    SOURCE_EXTS = {
        "py", "js", "jsx", "ts", "tsx", "mjs", "cjs", "java", "go", "rs", "php",
        "rb", "cs", "cpp", "c", "h", "hpp", "kt", "scala", "lua", "ex", "sh",
    }
    MANIFEST_NAMES = {
        "package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
        "dev-requirements.txt", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        "poetry.lock", "uv.lock", "pipfile", "pipfile.lock", "go.mod", "go.sum",
        "cargo.toml", "cargo.lock", "gemfile", "gemfile.lock", "composer.json",
        "composer.lock",
    }
    CONTEXT_NAMES = {
        "readme.md", "readme", "readme.rst", "license", "license.md", "makefile",
        "dockerfile", "contributing.md", "architecture.md", "changelog.md",
    }
    CONFIG_NAMES = {
        "dockerfile", "docker-compose.yml", "docker-compose.yaml", "makefile",
        ".env.example", ".env.sample", "tsconfig.json", "next.config.js",
        "next.config.mjs", "vite.config.ts", "setup.py", "setup.cfg", "tox.ini",
    }
    ENTRY_POINT_NAMES = {
        "main.py", "__main__.py", "app.py", "cli.py", "server.py", "run.py",
        "wsgi.py", "asgi.py", "manage.py", "index.ts", "index.tsx", "index.js",
        "index.jsx", "main.ts", "main.tsx", "main.js", "main.go", "main.rs",
    }

    def __init__(self) -> None:
        self.ingestion = IngestionEngine()
        self.enable_ai_analysis = os.getenv("CODELENS_ENABLE_AI_ANALYSIS", "true").lower() in {"1", "true", "yes", "on"}
        self.max_files = int(os.getenv("CODELENS_MAX_FILES", str(self.MAX_FILES)))
        self.max_file_bytes = int(os.getenv("CODELENS_MAX_FILE_BYTES", str(self.MAX_FILE_BYTES)))
        self.history_depth = int(os.getenv("CODELENS_GIT_HISTORY_DEPTH", "40"))
        self.include_churn_lines = os.getenv("CODELENS_GIT_CHURN_LINES", "false").lower() in {"1", "true", "yes", "on"}
        self.security_scanner = SecurityScanner()
        self.research = ResearchEngine()

        if self.enable_ai_analysis:
            self.ai_analyzer: AIAnalyzer | None = AIAnalyzer()
            if not self.ai_analyzer.available:
                print("Warning: NEBIUS_API_KEY is not set - running deterministic analysis only.")
        else:
            self.ai_analyzer = None

    # ------------------------------------------------------------------ #
    # pipeline
    # ------------------------------------------------------------------ #

    def analyze_repo(
        self,
        repo_url: str,
        progress_callback: Callable[[str], None] | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Measure a repository, then let a model review what was measured.

        ``refresh=True`` re-runs the whole pipeline. Otherwise a stored analysis
        of the same commit is returned, because re-measuring an unchanged
        repository would spend a clone, a history walk and a set of model calls to
        arrive at the same answer.
        """
        started = time.perf_counter()
        report = self._progress(progress_callback)

        repo_url = normalize_repo_url(repo_url)
        repo_id = self.ingestion.repo_id_for(repo_url)

        if not refresh:
            cached = self.ingestion.cached_snapshot(repo_url)
            if cached:
                report(
                    f"This repository is unchanged since it was analysed ({cached['head_sha'][:7]}); "
                    "reusing that analysis. Ask for a refresh to run it again."
                )
                return self._result_from_snapshot(cached, cached=True)

        repo_path = self.ingestion.clone_repo(repo_url, report)
        report("Repository cloned. Reading source files...")

        files, truncated = self._collect_files(repo_path, report)
        source_files = [file for file in files if self._is_source(file)]
        report(
            f"Parsed {len(source_files)} source files"
            + (f" (stopped at the {self.max_files}-file limit)" if truncated else "")
            + f" out of {len(files)} files read."
        )

        findings = self._prescan(files, report)
        report(f"Static analysis flagged {len(findings)} candidate issues.")

        graph = GraphBuilder(repo_path, source_files).build()
        report(
            f"Dependency graph: {graph.metrics()['link_count']} resolved imports "
            f"between {graph.metrics()['file_count']} files."
        )

        churn = self._churn(repo_path, report)
        self._annotate_files(files, graph, churn, findings)

        hotspots = rank_hotspots(
            source_files,
            self._graph_metrics(graph),
            churn,
            self._findings_by_file(findings),
        )
        report(f"Ranked {len(hotspots)} hotspots from complexity, churn and connectivity.")

        manifests = self._collect_dependency_manifests(files)
        context_files = self._collect_repo_context_files(files)
        tech_stack = self._detect_tech_stack(files, manifests)
        entry_points = self._detect_entry_points(files)

        architecture: dict[str, Any] = {}
        llm_usage: list[dict[str, Any]] = []

        if self.ai_analyzer is not None and self.ai_analyzer.available:
            self.ai_analyzer.set_progress_callback(progress_callback)
            context_files_for_model = self._files_for_context(files, findings)

            report(f"Model triage: asking {self.ai_analyzer.model_id} to review the candidates.")
            findings, model_findings, usage = self.ai_analyzer.triage_findings(
                findings,
                repo_info=repo_url,
                context_files=context_files_for_model,
            )
            llm_usage.extend(usage)
            for finding in model_findings:
                if finding not in findings:
                    findings.append(finding)
            findings = self._deduplicate_vulnerabilities(rank_findings(findings))
            report(f"Model triage reviewed the candidates; {len(findings)} findings retained.")

            report("Asking the model for an architecture summary.")
            architecture, usage = self.ai_analyzer.summarise_architecture(
                tech_stack=tech_stack,
                manifests=manifests,
                entry_points=entry_points,
                graph_metrics=graph.metrics(),
                hotspots=[hotspot.to_dict() for hotspot in hotspots],
                repo_info=repo_url,
                readme=self._readme_excerpt(context_files),
            )
            llm_usage.extend(usage)

            report("Asking the model to write the remediation advice.")
            fixes, usage = self.ai_analyzer.recommend_fixes(
                findings,
                repo_info=repo_url,
                context_files=context_files_for_model,
            )
            llm_usage.extend(usage)
            for finding in findings:
                fix = fixes.get(str(finding.get("id", "")))
                if not fix:
                    continue
                finding["recommendation"] = fix["recommendation"]
                if fix.get("effort"):
                    finding["fix_effort"] = fix["effort"]
                finding["fix_breaking_change"] = bool(fix.get("breaking_change"))
            report(f"Remediation written for {len(fixes)} of {len(findings)} findings.")

        dependency_research = self._research_dependencies(manifests, report)
        findings = self._ground_findings(findings, report)

        findings = self._deduplicate_vulnerabilities(rank_findings(findings))
        security_summary = summarize_findings(findings)
        self._attach_findings_to_files(files, findings)

        analyzed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stats = self._build_stats(
            files=files,
            findings=findings,
            security_summary=security_summary,
            graph=graph,
            hotspots=hotspots,
            manifests=manifests,
            dependency_research=dependency_research,
            head_sha=churn.head,
            analyzed_at=analyzed_at,
            elapsed=time.perf_counter() - started,
        )

        snapshot = self._build_snapshot(
            repo_id=repo_id,
            repo_url=repo_url,
            repo_path=repo_path,
            files=files,
            context_files=context_files,
            graph=graph,
            churn=churn,
            hotspots=hotspots,
            findings=findings,
            manifests=manifests,
            tech_stack=tech_stack,
            architecture=architecture,
            llm_usage=llm_usage,
            dependency_research=dependency_research,
            security_summary=security_summary,
            stats=stats,
            truncated=truncated,
            elapsed=time.perf_counter() - started,
        )
        self.ingestion.save_snapshot(repo_id, snapshot)
        return self._result_from_snapshot(snapshot, cached=False)

    @staticmethod
    def _result_from_snapshot(snapshot: dict[str, Any], cached: bool) -> dict[str, Any]:
        """The API payload, built from the snapshot either way.

        A run that reused a stored analysis and a run that just performed one
        therefore return the same keys, so the client never has to guess which
        kind of response it received.
        """
        return {
            "repo_id": snapshot.get("repo_id", ""),
            "repo_url": snapshot.get("repo_url", ""),
            "head_sha": snapshot.get("head_sha", ""),
            "files": snapshot.get("files", []),
            "graph": snapshot.get("graph", {}),
            "hotspots": snapshot.get("hotspots", []),
            "vulnerabilities": snapshot.get("vulnerabilities", []),
            "security_summary": snapshot.get("security_summary", {}),
            "dependency_manifests": snapshot.get("dependency_manifests", []),
            "tech_stack": snapshot.get("tech_stack", {}),
            "ai_dependencies": snapshot.get("ai_dependencies", {"vulnerable": [], "outdated": []}),
            "dependency_research": snapshot.get("dependency_research", {}),
            "architecture": snapshot.get("architecture", {}),
            "churn": snapshot.get("churn", {}),
            "llm_usage": snapshot.get("llm_usage", []),
            "truncated": bool(snapshot.get("truncated")),
            "cached": cached,
            "stats": snapshot.get("stats", {}),
        }

    def _build_stats(
        self,
        *,
        files: Sequence[dict[str, Any]],
        findings: Sequence[dict[str, Any]],
        security_summary: dict[str, Any],
        graph: DependencyGraph,
        hotspots: Sequence[Any],
        manifests: Sequence[dict[str, Any]],
        dependency_research: dict[str, Any],
        head_sha: str,
        analyzed_at: str,
        elapsed: float,
    ) -> dict[str, Any]:
        """The numbers the dashboard shows, counted once and stored with the snapshot."""
        by_severity = security_summary.get("by_severity", {})
        return {
            "total_files": len(files),
            "source_files": sum(1 for file in files if self._is_source(file)),
            "languages": self._count_languages(files),
            "total_vulnerabilities": len(findings),
            "critical_vulnerabilities": by_severity.get("CRITICAL", 0),
            "high_vulnerabilities": by_severity.get("HIGH", 0),
            "medium_vulnerabilities": by_severity.get("MEDIUM", 0),
            "static_findings": sum(1 for item in findings if item.get("source") == "static-analysis"),
            "model_findings": sum(1 for item in findings if item.get("source") == MODEL_FINDING_SOURCE),
            "dismissed_findings": sum(1 for item in findings if item.get("triage") == "dismissed"),
            "grounded_findings": sum(1 for item in findings if item.get("reference")),
            "hotspot_count": len(hotspots),
            "graph_links": graph.metrics()["link_count"],
            "dependency_manifests": len(manifests),
            "dependencies": sum(len(manifest.get("dependencies", [])) for manifest in manifests),
            "vulnerable_dependencies": len(dependency_research.get("vulnerable", [])),
            "outdated_dependencies": len(dependency_research.get("outdated", [])),
            "research_queries": int(dependency_research.get("queries_run", 0)),
            "total_loc": sum(int(file.get("loc", 0) or 0) for file in files if self._is_source(file)),
            "analyzed_at": analyzed_at,
            "elapsed_seconds": round(elapsed, 2),
            "head_sha": head_sha,
        }

    # ------------------------------------------------------------------ #
    # stages
    # ------------------------------------------------------------------ #

    def _collect_files(self, repo_path: str, report: Callable[[str], None]) -> tuple[list[dict[str, Any]], bool]:
        """Read and parse every analysable file in the clone."""
        files: list[dict[str, Any]] = []
        truncated = False

        for root, dirs, names in os.walk(repo_path):
            dirs[:] = sorted(directory for directory in dirs if directory not in self.SKIP_DIRS)

            for name in sorted(names):
                if len(files) >= self.max_files:
                    truncated = True
                    return files, truncated

                lowered = name.lower()
                extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                is_manifest = lowered in self.MANIFEST_NAMES
                is_config = lowered in self.CONTEXT_NAMES
                if extension not in self.SOURCE_EXTS and not is_manifest and not is_config:
                    continue

                absolute_path = os.path.join(root, name)
                size_limit = self.MAX_MANIFEST_BYTES if (is_manifest or is_config) else self.max_file_bytes
                try:
                    if os.path.getsize(absolute_path) > size_limit:
                        continue
                    with open(absolute_path, "r", errors="ignore") as handle:
                        content = handle.read()
                except OSError as error:
                    print(f"Skipping {name}: {error}")
                    continue

                relative_path = os.path.relpath(absolute_path, repo_path).replace(os.sep, "/")
                if is_generated_path(lowered, relative_path):
                    continue
                record = self._parse_file(relative_path, content, extension, is_manifest, is_config)
                files.append(record)

        return files, truncated

    def _parse_file(
        self,
        relative_path: str,
        content: str,
        extension: str,
        is_manifest: bool,
        is_context_file: bool,
    ) -> dict[str, Any]:
        """Tree-sitter facts where a grammar exists, regex imports where it does not."""
        language = language_for_path(relative_path) or (extension or "text")
        facts = parse_source_safely(language, content, relative_path)

        if facts is not None:
            imports = facts.imports
            record = {
                "file_path": relative_path,
                "language": _display_language(language),
                "content": content,
                "complexity": facts.complexity,
                "loc": facts.loc,
                "comment_lines": facts.comment_lines,
                "functions": [function.to_dict() for function in facts.functions],
                "classes": facts.classes,
                "imports": imports,
                "parsed_with": "tree-sitter",
            }
        else:
            imports = extract_imports_with_regex(content, extension)
            record = {
                "file_path": relative_path,
                "language": _display_language(language),
                "content": content,
                "complexity": 0,
                "loc": len(content.splitlines()),
                "comment_lines": 0,
                "functions": [],
                "classes": [],
                "imports": imports,
                "parsed_with": "regex",
            }

        if is_manifest:
            # A manifest is configuration, not source: it is parsed for declared
            # dependencies and kept out of complexity, churn and language counts.
            record["is_manifest"] = True
        elif is_context_file:
            record["is_document"] = True
            record["language"] = "document"
        return record

    def _prescan(self, files: Sequence[dict[str, Any]], report: Callable[[str], None]) -> list[dict[str, Any]]:
        """Deterministic security scan over every file, before any model runs."""
        findings: list[dict[str, Any]] = []
        for file in files:
            content = str(file.get("content", ""))
            if not content or file.get("is_document") or file.get("is_manifest"):
                continue
            findings.extend(
                self.security_scanner.scan_file(
                    str(file.get("file_path", "")),
                    content,
                    str(file.get("language", "")),
                )
            )
        return rank_findings(findings)

    def _churn(self, repo_path: str, report: Callable[[str], None]) -> ChurnReport:
        history = GitHistory(repo_path)
        if self.history_depth <= 0:
            report("Git history inspection disabled; churn is not part of the hotspot ranking.")
            return ChurnReport(head=history.head_sha(), reason="disabled by configuration")

        if report is not None and self.history_depth:
            report(f"Reading the last {self.history_depth} commits for change frequency...")

        report_data = history.churn(deepen=self.history_depth, include_lines=self.include_churn_lines)
        if report_data.available:
            report(
                f"Git history: {report_data.commits_scanned} commits, {len(report_data.files)} files changed."
                + (f" ({report_data.reason})" if report_data.reason else "")
            )
        else:
            report(f"Git history unavailable: {report_data.reason or 'no commits found'}.")
        return report_data

    def _graph_metrics(self, graph: DependencyGraph) -> dict[str, dict[str, Any]]:
        return {
            path: {"fan_in": node.fan_in, "fan_out": node.fan_out}
            for path, node in graph.nodes.items()
        }

    def _annotate_files(
        self,
        files: list[dict[str, Any]],
        graph: DependencyGraph,
        churn: ChurnReport,
        findings: Sequence[dict[str, Any]],
    ) -> None:
        """Attach per-file connectivity and churn so the UI can explain a file at a glance."""
        findings_by_file = self._findings_by_file(findings)
        for file in files:
            path = str(file.get("file_path", ""))
            node = graph.nodes.get(path)
            file["fan_in"] = node.fan_in if node else 0
            file["fan_out"] = node.fan_out if node else 0
            churn_entry = churn.files.get(path)
            file["churn"] = churn_entry.to_dict() if churn_entry else {"commits": 0, "added": 0, "removed": 0, "total_changes": 0, "authors": 0, "last_commit": ""}
            file["finding_count"] = len(findings_by_file.get(path, []))

    def _attach_findings_to_files(self, files: list[dict[str, Any]], findings: Sequence[dict[str, Any]]) -> None:
        findings_by_file = self._findings_by_file(findings)
        for file in files:
            file["vulnerabilities"] = findings_by_file.get(str(file.get("file_path", "")), [])

    def _files_for_context(self, files: Sequence[dict[str, Any]], findings: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """The files most worth putting in front of the model: entry points, the
        files holding findings, the most complex files and the most connected ones."""
        findings_by_file = self._findings_by_file(findings)
        scored: list[tuple[int, dict[str, Any]]] = []

        for file in files:
            path = str(file.get("file_path", ""))
            if not path or file.get("is_document"):
                continue
            score = 0
            basename = os.path.basename(path).lower()
            if basename in self.ENTRY_POINT_NAMES:
                score += 40
            score += min(20, len(findings_by_file.get(path, [])) * 8)
            score += min(15, int(file.get("complexity", 0) or 0) / 8)
            score += min(10, int(file.get("fan_in", 0) or 0))
            if file.get("is_manifest"):
                # Manifests say which versions the project pinned, which is the
                # one thing source files cannot tell the model.
                score += 5
            if score > 0:
                scored.append((score, file))

        scored.sort(key=lambda item: (-item[0], str(item[1].get("file_path", ""))))
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _, file in scored:
            path = str(file.get("file_path", ""))
            if path in seen:
                continue
            seen.add(path)
            selected.append(file)
        return selected

    # ------------------------------------------------------------------ #
    # snapshot
    # ------------------------------------------------------------------ #

    def _build_snapshot(
        self,
        repo_id: str,
        repo_url: str,
        repo_path: str,
        files: list[dict[str, Any]],
        context_files: list[dict[str, Any]],
        graph: DependencyGraph,
        churn: ChurnReport,
        hotspots: Sequence[Any],
        findings: list[dict[str, Any]],
        manifests: list[dict[str, Any]],
        tech_stack: dict[str, Any],
        architecture: dict[str, Any],
        llm_usage: list[dict[str, Any]],
        dependency_research: dict[str, Any],
        security_summary: dict[str, Any],
        stats: dict[str, Any],
        truncated: bool,
        elapsed: float,
    ) -> dict[str, Any]:
        head_sha = churn.head or ""
        return {
            "repo_id": repo_id,
            "repo_url": repo_url,
            "repo_path": repo_path,
            "head_sha": head_sha,
            "analyzed_at": stats.get("analyzed_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_seconds": round(elapsed, 2),
            "truncated": truncated,
            "files": self._public_files(files),
            "special_files": context_files,
            "graph": graph.to_dict(),
            "hotspots": [hotspot.to_dict() for hotspot in hotspots],
            "vulnerabilities": findings,
            "security_summary": security_summary,
            "dependency_manifests": manifests,
            "dependency_research": dependency_research,
            "ai_dependencies": self._normalize_dependency_payload(dependency_research),
            "tech_stack": tech_stack,
            "architecture": architecture,
            "churn": churn.to_dict(),
            "llm_usage": llm_usage,
            "stats": stats,
            "model_available": bool(self.ai_analyzer and self.ai_analyzer.available),
            "research_available": bool(self.research.available),
        }

    def _public_files(self, files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """File records for the API and the snapshot: metrics, never source text."""
        return [
            {
                "file_path": file.get("file_path"),
                "language": file.get("language"),
                "imports": file.get("imports", []),
                "complexity": file.get("complexity", 0),
                "loc": file.get("loc", 0),
                "comment_lines": file.get("comment_lines", 0),
                "functions": file.get("functions", []),
                "classes": file.get("classes", []),
                "fan_in": file.get("fan_in", 0),
                "fan_out": file.get("fan_out", 0),
                "churn": file.get("churn", {}),
                "vulnerabilities": len(file.get("vulnerabilities", []) or []),
                "finding_count": file.get("finding_count", 0),
                "parsed_with": file.get("parsed_with", "regex"),
            }
            for file in files
        ]

    # ------------------------------------------------------------------ #
    # manifests, stack, entry points
    # ------------------------------------------------------------------ #

    def _collect_repo_context_files(self, files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """README, license and build instructions: cheap context for the model."""
        context_files: list[dict[str, Any]] = []
        priority = {
            "readme.md": 100,
            "readme": 100,
            "readme.rst": 99,
            "architecture.md": 90,
            "contributing.md": 80,
            "license": 70,
            "license.md": 70,
            "makefile": 60,
            "dockerfile": 60,
            "changelog.md": 40,
        }
        for file in files:
            basename = os.path.basename(str(file.get("file_path", ""))).lower()
            score = priority.get(basename, 0)
            if not score:
                continue
            context_files.append(
                {
                    "file_path": file.get("file_path"),
                    "language": file.get("language", "document"),
                    "priority": score,
                    "content": str(file.get("content", ""))[:4000],
                }
            )
        context_files.sort(key=lambda item: -int(item.get("priority", 0)))
        return context_files

    def _collect_dependency_manifests(self, files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        manifests: list[dict[str, Any]] = []
        for file in files:
            file_path = str(file.get("file_path", ""))
            basename = os.path.basename(file_path).lower()
            content = str(file.get("content", ""))

            if basename == "package.json":
                manifest = self._parse_package_json(file_path, content)
            elif basename in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "composer.lock", "pipfile.lock"}:
                manifest = self._parse_lockfile(file_path, basename, content)
            elif basename in {"uv.lock", "poetry.lock", "cargo.lock"}:
                manifest = self._parse_toml_lock(file_path, basename, content)
            elif basename in {"requirements.txt", "requirements-dev.txt", "dev-requirements.txt"}:
                manifest = self._parse_requirements_file(file_path, content)
            elif basename == "pyproject.toml":
                manifest = self._parse_pyproject_toml(file_path, content)
            elif basename in {"gemfile", "gemfile.lock", "pipfile"}:
                manifest = self._parse_gemfile(file_path, content)
            elif basename in {"composer.json", "composer.lock"}:
                manifest = self._parse_composer_json(file_path, content)
            elif basename == "go.mod":
                manifest = self._parse_go_mod(file_path, content)
            elif basename == "go.sum":
                manifest = self._parse_go_sum(file_path, content)
            elif basename == "cargo.toml":
                manifest = self._parse_cargo_toml(file_path, content)
            else:
                manifest = {}

            if manifest:
                manifests.append(manifest)
        return manifests

    def _detect_tech_stack(self, files: Sequence[dict[str, Any]], manifests: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """The stack, read from the repository rather than guessed by a model."""
        languages = self._count_languages(files)
        frameworks: list[str] = []
        signals: dict[str, list[str]] = {}

        package_names = {
            "react", "next", "vue", "svelte", "angular", "express", "fastify",
            "nest", "django", "flask", "fastapi", "starlette", "torch",
            "transformers", "openai", "tailwindcss", "redux", "prisma", "graphql",
        }
        for manifest in manifests:
            for dependency in manifest.get("dependencies", []):
                name = str(dependency.get("name", "")).lower()
                if not name:
                    continue
                signals.setdefault(name, []).append(str(manifest.get("file_path", "")))
                if name in package_names and name not in frameworks:
                    frameworks.append(name)

        source_langs = {language: count for language, count in languages.items() if language not in {"document", "text"}}
        total = sum(source_langs.values()) or 1
        return {
            "languages": languages,
            "primary_languages": [
                language for language, _ in sorted(source_langs.items(), key=lambda item: -item[1])[:4]
            ],
            "framework_hints": frameworks,
            "dependency_evidence": {name: paths[:2] for name, paths in list(signals.items())[:40]},
            "manifest_files": [str(manifest.get("file_path", "")) for manifest in manifests],
            "manifest_count": sum(1 for file in files if file.get("is_manifest")),
            "document_count": sum(1 for file in files if file.get("is_document")),
            "python_share": round(
                (source_langs.get("python", 0) + source_langs.get("ipynb", 0)) / total, 3
            ),
        }

    def _research_dependencies(self, manifests: Sequence[dict[str, Any]], report: Callable[[str], None]) -> dict[str, Any]:
        """Check what the project pinned against published advisories and current versions.

        This is a Tavily search, not a vulnerability database: an entry only
        appears as vulnerable when the returned text names both the package and a
        CVE, and it carries the quote and the links so the range can be confirmed.
        """
        if not self.research.available:
            report("Tavily is not configured, so dependencies are reported without web verification.")
            return {
                "provider": "tavily",
                "enabled": False,
                "vulnerable": [],
                "outdated": [],
                "checked": [],
                "unverified": [],
                "queries_run": 0,
                "declared_dependencies": sum(len(manifest.get("dependencies", [])) for manifest in manifests),
                "not_checked": sum(len(manifest.get("dependencies", [])) for manifest in manifests),
                "errors": [],
                "note": "Set TAVILY_API_KEY to check pinned dependencies against published advisories.",
            }

        report("Asking Tavily about the dependencies this project pins...")
        result = self.research.verify_dependencies(manifests)
        report(
            f"Dependency check: {result['queries_run']} searches, "
            f"{len(result['vulnerable'])} with advisories, {len(result['outdated'])} behind current."
        )
        return result

    def _ground_findings(self, findings: list[dict[str, Any]], report: Callable[[str], None]) -> list[dict[str, Any]]:
        """Attach a link to authoritative guidance to the most severe findings."""
        if not self.research.available or not findings:
            return findings

        grounded = self.research.ground_findings(findings)
        if not grounded:
            return findings

        for finding in findings:
            reference = grounded.get(str(finding.get("id", "")))
            if reference:
                finding["reference"] = reference
        report(f"Linked guidance to {len(grounded)} findings.")
        return findings

    def _readme_excerpt(self, context_files: Sequence[dict[str, Any]]) -> str:
        """The README, if there is one: the cheapest honest description of intent."""
        for file in context_files:
            basename = os.path.basename(str(file.get("file_path", ""))).lower()
            if basename.startswith("readme"):
                return str(file.get("content", ""))
        return ""

    def _detect_entry_points(self, files: Sequence[dict[str, Any]]) -> list[str]:
        entry_points: list[str] = []
        for file in files:
            path = str(file.get("file_path", ""))
            if not path:
                continue
            basename = os.path.basename(path).lower()
            if basename in self.ENTRY_POINT_NAMES and path not in entry_points:
                entry_points.append(path)
        return entry_points[:12]

    # ------------------------------------------------------------------ #
    # manifest parsers
    # ------------------------------------------------------------------ #

    def _parse_package_json(self, file_path: str, content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except Exception:
            return {}

        dependencies = []
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            values = parsed.get(section, {})
            if not isinstance(values, dict):
                continue
            for name, version in values.items():
                dependencies.append({"name": str(name), "version": str(version), "source": section})

        metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
        if not dependencies:
            return {}

        return {
            "file_path": file_path,
            "type": "package.json",
            "ecosystem": "npm",
            "name": str(parsed.get("name", "")),
            "dependencies": dependencies,
            "scripts": sorted((parsed.get("scripts") or {}).keys())[:20] if isinstance(parsed.get("scripts"), dict) else [],
            "engines": metadata.get("engines", {}) if metadata else {},
        }

    def _parse_requirements_file(self, file_path: str, content: str) -> dict[str, Any]:
        dependencies = []
        for line in content.splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or raw.startswith("-"):
                continue

            raw = raw.split("#", 1)[0].strip()
            if not raw or raw.startswith(("http://", "https://", "-e", ".")):
                continue
            match = re.match(r"^([A-Za-z0-9_.-]+)\s*([<>=!~;\[].*)?$", raw)
            if not match:
                continue

            dependencies.append(
                {
                    "name": match.group(1),
                    "version": (match.group(2) or "").strip(),
                    "source": os.path.basename(file_path),
                }
            )

        if not dependencies:
            return {}

        return {
            "file_path": file_path,
            "type": os.path.basename(file_path),
            "ecosystem": "pypi",
            "dependencies": dependencies,
        }

    def _parse_pyproject_toml(self, file_path: str, content: str) -> dict[str, Any]:
        if tomllib is None:
            return {}

        try:
            parsed = tomllib.loads(content)
        except Exception:
            return {}

        dependencies: list[dict[str, str]] = []
        project = parsed.get("project", {}) if isinstance(parsed.get("project"), dict) else {}

        project_deps = project.get("dependencies", [])
        if isinstance(project_deps, list):
            for item in project_deps:
                if not isinstance(item, str):
                    continue
                name, version = self._split_dependency_string(item)
                if name:
                    dependencies.append({"name": name, "version": version, "source": "project.dependencies"})

        optional_deps = project.get("optional-dependencies", {})
        if isinstance(optional_deps, dict):
            for extra, items in optional_deps.items():
                for item in items if isinstance(items, list) else []:
                    if not isinstance(item, str):
                        continue
                    name, version = self._split_dependency_string(item)
                    if name:
                        dependencies.append(
                            {"name": name, "version": version, "source": f"optional-dependencies.{extra}"}
                        )

        tool = parsed.get("tool", {}) if isinstance(parsed.get("tool"), dict) else {}
        poetry = tool.get("poetry", {}) if isinstance(tool.get("poetry"), dict) else {}
        poetry_deps = poetry.get("dependencies", {})
        if isinstance(poetry_deps, dict):
            for name, version in poetry_deps.items():
                if str(name).lower() == "python":
                    continue
                dependencies.append(
                    {
                        "name": str(name),
                        "version": self._stringify_dependency_version(version),
                        "source": "tool.poetry.dependencies",
                    }
                )

        poetry_group = tool.get("poetry", {}).get("group", {}) if isinstance(tool.get("poetry"), dict) else {}
        if isinstance(poetry_group, dict):
            for group, payload in poetry_group.items():
                group_deps = payload.get("dependencies", {}) if isinstance(payload, dict) else {}
                for name, version in group_deps.items():
                    if str(name).lower() == "python":
                        continue
                    dependencies.append(
                        {
                            "name": str(name),
                            "version": self._stringify_dependency_version(version),
                            "source": f"tool.poetry.group.{group}",
                        }
                    )

        if not dependencies:
            return {}

        return {
            "file_path": file_path,
            "type": "pyproject.toml",
            "ecosystem": "pypi",
            "name": str(project.get("name", "")),
            "dependencies": dependencies,
        }

    def _parse_lockfile(self, file_path: str, basename: str, content: str) -> dict[str, Any]:
        dependencies: list[dict[str, str]] = []

        if basename == "package-lock.json":
            try:
                parsed = json.loads(content)
            except Exception:
                return {}

            packages = parsed.get("packages", {}) if isinstance(parsed, dict) else {}
            if isinstance(packages, dict):
                for package_path, meta in packages.items():
                    if package_path in {"", "."} or not isinstance(meta, dict):
                        continue
                    dependencies.append(
                        {
                            "name": str(meta.get("name") or os.path.basename(package_path) or package_path),
                            "version": str(meta.get("version") or ""),
                            "source": "package-lock.json",
                        }
                    )
            elif isinstance(parsed.get("dependencies"), dict):
                for name, meta in parsed["dependencies"].items():
                    meta = meta if isinstance(meta, dict) else {}
                    dependencies.append(
                        {"name": str(name), "version": str(meta.get("version", "")), "source": "package-lock.json"}
                    )

        elif basename == "pnpm-lock.yaml":
            for match in re.finditer(r"^\s{2,}/?([\w@./-]+)@([\w.\-+]+):", content, re.MULTILINE):
                dependencies.append(
                    {"name": match.group(1).split("/")[-1], "version": match.group(2), "source": "pnpm-lock.yaml"}
                )

        elif basename == "yarn.lock":
            for line in content.splitlines():
                if line and not line.startswith((" ", "\t", "#")) and line.endswith(":"):
                    spec = line.rstrip(":").split(",")[0].strip().strip('"')
                    name, _, version = spec.rpartition("@")
                    if name:
                        dependencies.append(
                            {"name": name, "version": version.lstrip("^~"), "source": "yarn.lock"}
                        )

        elif basename == "pipfile.lock":
            try:
                parsed = json.loads(content)
            except Exception:
                return {}
            for name, meta in (parsed.get("default", {}) or {}).items():
                dependencies.append(
                    {"name": str(name), "version": str((meta or {}).get("version", "")), "source": "pipfile.lock"}
                )

        if not dependencies:
            return {}

        return {
            "file_path": file_path,
            "type": basename,
            "ecosystem": "npm" if basename != "pipfile.lock" else "pypi",
            "dependencies": dependencies,
            "resolved": True,
        }

    def _parse_toml_lock(self, file_path: str, basename: str, content: str) -> dict[str, Any]:
        """uv.lock, poetry.lock and Cargo.lock all list resolved packages in TOML."""
        if tomllib is None:
            return {}
        try:
            parsed = tomllib.loads(content)
        except Exception:
            return {}

        packages = parsed.get("package")
        if not isinstance(packages, list):
            return {}

        ecosystem = {
            "uv.lock": "pypi",
            "poetry.lock": "pypi",
            "cargo.lock": "crates.io",
        }.get(basename, "unknown")

        dependencies: list[dict[str, str]] = []
        for package in packages:
            if not isinstance(package, dict) or not package.get("name"):
                continue
            dependencies.append(
                {
                    "name": str(package["name"]),
                    "version": str(package.get("version", "")),
                    "source": basename,
                }
            )

        if not dependencies:
            return {}
        return {
            "file_path": file_path,
            "type": basename,
            "ecosystem": ecosystem,
            "dependencies": dependencies,
            "resolved": True,
        }

    def _parse_gemfile(self, file_path: str, content: str) -> dict[str, Any]:
        dependencies: list[dict[str, str]] = []
        for match in re.finditer(r"^\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", content, re.MULTILINE):
            dependencies.append(
                {"name": match.group(1), "version": match.group(2) or "", "source": os.path.basename(file_path)}
            )
        if not dependencies:
            return {}
        return {
            "file_path": file_path,
            "type": os.path.basename(file_path),
            "ecosystem": "rubygems",
            "dependencies": dependencies,
        }

    def _parse_composer_json(self, file_path: str, content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except Exception:
            return {}

        dependencies: list[dict[str, str]] = []
        for section in ("require", "require-dev"):
            values = parsed.get(section, {})
            if isinstance(values, dict):
                for name, version in values.items():
                    dependencies.append({"name": str(name), "version": str(version), "source": section})

        # composer.lock carries the resolved versions.
        for entry in parsed.get("packages", []) or []:
            if isinstance(entry, dict) and entry.get("name"):
                dependencies.append(
                    {"name": str(entry["name"]), "version": str(entry.get("version", "")), "source": "packages"}
                )

        if not dependencies:
            return {}
        return {
            "file_path": file_path,
            "type": os.path.basename(file_path),
            "ecosystem": "packagist",
            "dependencies": dependencies,
            "resolved": str(file_path).endswith("composer.lock"),
        }

    def _parse_go_sum(self, file_path: str, content: str) -> dict[str, Any]:
        """go.sum lists module/version pairs, which is the only pinned Go evidence."""
        dependencies: list[dict[str, str]] = []
        for line in content.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[1].endswith("/go.mod"):
                continue
            dependencies.append({"name": parts[0], "version": parts[1].lstrip("v"), "source": "go.sum"})
        if not dependencies:
            return {}
        return {
            "file_path": file_path,
            "type": "go.sum",
            "ecosystem": "go",
            "dependencies": dependencies,
            "resolved": True,
        }

    def _parse_go_mod(self, file_path: str, content: str) -> dict[str, Any]:
        dependencies: list[dict[str, str]] = []
        block = re.search(r"require\s*\((.*?)\)", content, re.DOTALL)
        if block:
            for line in block.group(1).splitlines():
                stripped = line.split("//")[0].strip()
                if not stripped:
                    continue
                parts = stripped.split()
                if len(parts) >= 2:
                    dependencies.append({"name": parts[0], "version": parts[1], "source": "go.mod"})
        for match in re.finditer(r"^\s*require\s+(\S+)\s+(\S+)", content, re.MULTILINE):
            dependencies.append({"name": match.group(1), "version": match.group(2), "source": "go.mod"})

        if not dependencies:
            return {}
        return {"file_path": file_path, "type": "go.mod", "ecosystem": "go", "dependencies": dependencies, "resolved": True}

    def _parse_cargo_toml(self, file_path: str, content: str) -> dict[str, Any]:
        if tomllib is None:
            return {}
        try:
            parsed = tomllib.loads(content)
        except Exception:
            return {}

        dependencies: list[dict[str, str]] = []
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            values = parsed.get(section, {})
            if isinstance(values, dict):
                for name, spec in values.items():
                    version = spec if isinstance(spec, str) else str((spec or {}).get("version", ""))
                    dependencies.append({"name": str(name), "version": version, "source": f"cargo.{section}"})

        if not dependencies:
            return {}
        return {"file_path": file_path, "type": "Cargo.toml", "ecosystem": "crates.io", "dependencies": dependencies}

    def _split_dependency_string(self, value: str) -> tuple[str, str]:
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*(\[[^\]]*\])?\s*(.*)$", value.strip())
        if not match:
            return "", ""
        return match.group(1), (match.group(3) or "").strip()

    def _stringify_dependency_version(self, value: Any) -> str:
        if isinstance(value, dict):
            if value.get("version"):
                return str(value["version"])
            extras = []
            if value.get("python"):
                extras.append(f"python {value['python']}")
            if value.get("markers"):
                extras.append(str(value["markers"]))
            return ", ".join(extras)
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _normalize_dependency_payload(self, research: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """The dependency section the UI renders, in one stable shape.

        The research report is the only source of these entries, so the keys it
        uses are passed straight through and an absent provider still produces
        the two lists the client expects.
        """
        normalized: dict[str, list[dict[str, Any]]] = {"vulnerable": [], "outdated": []}
        if not isinstance(research, dict):
            return normalized

        for key in ("vulnerable", "outdated"):
            items = research.get(key, [])
            if isinstance(items, list):
                normalized[key] = [item for item in items if isinstance(item, dict) and item.get("name")]
        return normalized

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _findings_by_file(self, findings: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            file_path = str(finding.get("file_path", "")).strip()
            if not file_path:
                continue
            grouped.setdefault(file_path, []).append(finding)
        return grouped

    def _deduplicate_vulnerabilities(self, vulnerabilities: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse restatements of the same issue into one finding.

        Two findings are the same when they point at the same file and line and
        either share a name or one of them came from the model review: a model
        that re-reports a static finding should strengthen it, not duplicate it.
        """
        unique: list[dict[str, Any]] = []
        by_location: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for finding in vulnerabilities:
            file_path = str(finding.get("file_path", "")).strip()
            line = str(finding.get("line", ""))
            name = str(finding.get("name", "")).strip().lower()
            source = str(finding.get("source", ""))

            bucket = by_location.setdefault((file_path, line), [])
            existing = next(
                (
                    candidate
                    for candidate in bucket
                    if str(candidate.get("name", "")).strip().lower() == name
                    or source == MODEL_FINDING_SOURCE
                    or str(candidate.get("source", "")) == MODEL_FINDING_SOURCE
                ),
                None,
            )

            if existing is None:
                bucket.append(finding)
                unique.append(finding)
                continue

            merged = self._merge_finding(existing, finding)
            if merged is existing:
                # The model restated a static finding: keep the static evidence.
                if str(finding.get("triage", "")) not in {"unverified", ""} and finding.get("triage_note"):
                    existing["triage_note"] = str(finding["triage_note"])
            else:
                bucket[bucket.index(existing)] = merged
                if merged not in unique:
                    unique.append(merged)

        return unique

    @staticmethod
    def _merge_finding(kept: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
        """Keep the higher severity and the richest text of two identical findings."""
        winner, loser = (other, kept) if _severity_rank(other) < _severity_rank(kept) else (kept, other)

        for key, value in loser.items():
            if key in {"snippet", "recommendation", "description", "triage_note"} and not winner.get(key):
                winner[key] = value
        if loser.get("triage") in {"confirmed", "escalated"} and winner.get("triage") not in {"confirmed", "escalated"}:
            winner["triage"] = loser["triage"]
        if loser.get("source") == "static-analysis" and winner.get("source") == MODEL_FINDING_SOURCE:
            winner["source"] = "static-analysis"
        return winner

    def _count_languages(self, files: Sequence[dict[str, Any]]) -> dict[str, int]:
        """Counts of source languages only: a README is not a language."""
        counts: dict[str, int] = {}
        for file in files:
            if not self._is_source(file):
                continue
            language = str(file.get("language", "unknown"))
            counts[language] = counts.get(language, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))

    @staticmethod
    def _is_source(file: dict[str, Any]) -> bool:
        """Source files carry the metrics; manifests and documents describe them."""
        return not file.get("is_document") and not file.get("is_manifest")

    @staticmethod
    def _progress(callback: Callable[[str], None] | None) -> Callable[[str], None]:
        if callback is None:
            return lambda message: print(message, flush=True)

        def report(message: str) -> None:
            print(message, flush=True)
            try:
                callback(message)
            except Exception as error:  # noqa: BLE001 - progress must not fail a run
                print(f"progress callback failed: {error}")

        return report

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            match = re.search(r"(\d+)", str(value))
            return int(match.group(1)) if match else default


def _display_language(language: str) -> str:
    return {
        "python": "python",
        "javascript": "javascript",
        "typescript": "typescript",
        "tsx": "typescript",
        "c_sharp": "csharp",
        "cpp": "cpp",
    }.get(language, language or "text")


def _severity_rank(finding: dict[str, Any]) -> int:
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    return order.get(str(finding.get("severity", "LOW")).upper(), 9)
