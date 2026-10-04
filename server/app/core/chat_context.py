"""Building model context from a stored repository snapshot.

Two jobs live here:

* reading files out of a cloned snapshot safely (``read_repo_file``, ``resolve_repo_file``);
* choosing which files are worth sending to a model for a given question
  (``select_context_files``, ``build_file_context``, ``build_general_context``).

Keeping this out of the HTTP layer means the chat endpoint, the Fix Advisor and the
tests all select context the same way.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Sequence

from .parser import is_generated_path

# Extensions worth reading for context, plus the manifest-ish files that describe
# the project rather than implement it.
CONTEXT_EXTENSIONS = {
    "py", "js", "jsx", "ts", "tsx", "mjs", "cjs", "json", "md", "txt",
    "toml", "yml", "yaml", "css", "html", "env",
}
CONTEXT_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".next", "dist", "build",
    "coverage", "venv", ".venv",
}
CONTEXT_FILE_NAMES = {"readme", "readme.md", "license", "makefile", "dockerfile"}

LANGUAGES = {
    "py": "python",
    "js": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "mjs": "javascript",
    "cjs": "javascript",
    "json": "json",
    "md": "markdown",
    "toml": "toml",
    "yml": "yaml",
    "yaml": "yaml",
    "css": "css",
    "html": "html",
}

STOP_WORDS = {
    "the", "and", "for", "with", "from", "this", "that", "these", "those",
    "please", "can", "you", "tell", "about", "show", "read", "all", "file",
    "files", "project", "repo", "repository", "my", "different", "need",
    "want", "access", "look", "see", "what", "does", "do", "how", "why",
    "where", "which", "when", "whole", "its", "are", "was", "were", "there",
    "here", "explain", "describe", "give", "tell", "code", "codebase", "used",
    "using", "use", "work", "works", "code", "happens", "happen", "app",
}

# Queries that should get the whole-repository view rather than two files.
BROAD_MARKERS = (
    "all files", "related", "everything", "entire", "broad", "overview",
    "analyze", "analyse", "read all", "scan", "chat", "conversation",
    "architecture", "structure", "how does", "how do", "what does",
    "how is", "how are", "where is", "why is", "data flow", "entry point",
    "big picture", "summarise", "summarize", "explain the", "walk me",
)


def is_broad_query(query: str) -> bool:
    lowered = (query or "").lower()
    if any(marker in lowered for marker in BROAD_MARKERS):
        return True
    # A question with no file-shaped words in it is a question about the project.
    return not any(token in lowered for token in ("/", ".", "_", "-")) and bool(
        re.search(r"\b(what|how|why|where|which|explain|describe|summarise|summarize)\b", lowered)
    )


MAX_FILE_BYTES = 200_000


def guess_language(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    return LANGUAGES.get(ext, ext or "text")


def resolve_repo_file(repo_path: str, relative_path: str) -> tuple[str, str]:
    """Resolve ``relative_path`` inside ``repo_path``.

    Returns ``(absolute_path, repo_relative_path)`` or ``("", "")`` when the file
    is missing or the path escapes the repository root.
    """
    if not repo_path or not relative_path:
        return "", ""

    root = os.path.realpath(repo_path)
    full_path = os.path.realpath(os.path.join(root, os.path.normpath(relative_path).lstrip("/\\")))
    if not _is_inside(root, full_path):
        return "", ""
    if not os.path.isfile(full_path):
        return "", ""

    return full_path, os.path.relpath(full_path, root)


def _is_inside(root: str, candidate: str) -> bool:
    """True when ``candidate`` is ``root`` itself or lives underneath it."""
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:
        # Different drives on Windows, or a mix of absolute and relative paths.
        return False


def read_repo_file(repo_path: str, relative_path: str, max_bytes: int | None = 12_000) -> str:
    """Read a file from a snapshot, or return an empty string if it is not readable."""
    full_path, resolved = resolve_repo_file(repo_path, relative_path)
    if not full_path:
        return ""
    try:
        with open(full_path, "r", errors="ignore") as handle:
            return handle.read() if max_bytes is None else handle.read(max_bytes)
    except OSError:
        return ""


def collect_repo_file_paths(repo_path: str) -> list[str]:
    """List candidate context files that exist on disk but may be missing from a snapshot."""
    if not repo_path or not os.path.isdir(repo_path):
        return []

    collected: list[str] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [directory for directory in dirs if directory not in CONTEXT_SKIP_DIRS]
        for file_name in files:
            full_path = os.path.join(root, file_name)
            try:
                if os.path.getsize(full_path) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
            if ext not in CONTEXT_EXTENSIONS and file_name.lower() not in CONTEXT_FILE_NAMES and not file_name.startswith(".env"):
                continue
            relative_path = os.path.relpath(full_path, repo_path)
            if is_generated_path(file_name, relative_path):
                continue
            collected.append(relative_path)
    return collected


def query_terms(query: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.split(r"[^a-zA-Z0-9_.-]+", (query or "").lower()):
        token = raw.strip("._-")
        if len(token) < 2 or token in STOP_WORDS or token in tokens:
            continue
        tokens.append(token)
    return tokens


def _segments(path: str) -> set[str]:
    """The meaningful words in a path: ``app/services/user_service.py`` -> {app, services, user, service}."""
    return {segment for segment in re.split(r"[/\\._\-]+", path.lower()) if segment}


def _score_file(file_path: str, terms: Sequence[str]) -> int:
    """Rank a path against the terms of a question.

    Matching whole path segments is what keeps ``do`` from matching ``vendor``:
    a raw substring test makes almost every path look relevant to every query.
    """
    lowered = file_path.lower()
    basename = os.path.basename(lowered)
    stem = os.path.splitext(basename)[0]
    segments = _segments(lowered)

    score = 0
    for term in terms:
        if term in segments:
            score += 8 if term == stem else 5
        elif len(term) >= 4 and any(segment.startswith(term) or term.startswith(segment) for segment in segments):
            score += 3
        elif len(term) >= 5 and term in basename:
            score += 4

    if basename in {"readme.md", "package.json", "requirements.txt", "pyproject.toml", "readme"}:
        score += 1
    return score


def select_context_files(snapshot: dict[str, Any], query: str, max_files: int = 10) -> list[dict[str, Any]]:
    """Rank snapshot files (plus any extra files on disk) by relevance to ``query``."""
    files = list(snapshot.get("files", []))
    repo_path = snapshot.get("repo_path", "")

    if repo_path and os.path.isdir(repo_path):
        known = {str(file_info.get("file_path", "")) for file_info in files}
        for file_path in collect_repo_file_paths(repo_path):
            if file_path not in known:
                files.append(
                    {
                        "file_path": file_path,
                        "language": guess_language(file_path),
                        "imports": [],
                        "complexity": 0,
                        "functions": [],
                        "classes": [],
                        "vulnerabilities": 0,
                    }
                )

    terms = query_terms(query)
    broad = is_broad_query(query)

    # Entry points and extension points named by the architecture pass are the
    # files a newcomer asks about, so they are always in the running.
    structural = {str(path) for path in structural_files(snapshot)}

    scored = [
        (score, file_info)
        for file_info in files
        if (score := _score_file(str(file_info.get("file_path", "")), terms)) > 0
        or (broad and str(file_info.get("file_path", "")) in structural)
    ]

    if not scored and broad:
        special = {str(item.get("file_path", "")) for item in snapshot.get("special_files", []) or []}
        scored = [
            (1, file_info)
            for file_info in files
            if str(file_info.get("file_path", "")) in special or str(file_info.get("file_path", "")) in structural
        ]

    scored.sort(key=lambda item: (-item[0], str(item[1].get("file_path", ""))))

    limit = max_files if broad else min(max_files, 6)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, file_info in scored:
        file_path = str(file_info.get("file_path", ""))
        if not file_path or file_path in seen:
            continue
        seen.add(file_path)
        selected.append(file_info)
        if len(selected) >= limit:
            break

    return selected or files[:limit]


def structural_files(snapshot: dict[str, Any]) -> list[str]:
    """Files the architecture pass identified as entry or extension points."""
    architecture = snapshot.get("architecture") or {}
    paths: list[str] = []
    for key in ("entry_points", "extension_points", "risks"):
        for item in architecture.get(key, []) or []:
            path = str(item.get("file_path", "")) if isinstance(item, dict) else ""
            if path and path not in paths:
                paths.append(path)
    return paths


def build_file_context(
    snapshot: dict[str, Any],
    targets: Sequence[dict[str, Any]],
    query: str,
) -> tuple[str, list[str]]:
    """Return ``(context_text, paths_used)`` for the matched files and their neighbours."""
    repo_path = snapshot.get("repo_path", "")
    neighbors = build_graph_neighbors(snapshot)
    files_by_path = {str(file.get("file_path", "")): file for file in snapshot.get("files", [])}

    broad = is_broad_query(query)
    max_files = 12 if broad else 6
    selected_targets = list(targets)[:max_files]

    context_paths: list[str] = []
    sections: list[str] = []
    digest = build_analysis_digest(snapshot)
    if digest:
        sections.append(digest)
    if len(selected_targets) > 1:
        sections.append(
            f"Matched {len(selected_targets)} files for this request. Reading the strongest matches and their "
            "nearby context instead of forcing a single file path."
        )

    total_chars = 0
    max_total_chars = 32_000 if broad else 18_000

    for target in selected_targets:
        target_path = str(target.get("file_path", ""))
        if not target_path or target_path in context_paths:
            continue

        context_paths.append(target_path)
        sections.append(f"\nFile: {target_path}")
        target_content = read_repo_file(repo_path, target_path)
        if target_content:
            clipped = target_content[:6000]
            sections.append(clipped)
            total_chars += len(clipped)

        related = [
            path
            for path in sorted(neighbors.get(target_path, set()))
            if path in files_by_path and path != target_path
        ]
        same_dir = os.path.dirname(target_path)
        related.extend(
            str(file.get("file_path", ""))
            for file in snapshot.get("files", [])
            if str(file.get("file_path", "")) != target_path
            and os.path.dirname(str(file.get("file_path", ""))) == same_dir
        )

        added_related = 0
        for path in related:
            if added_related >= 2 or total_chars >= max_total_chars:
                break
            if path in context_paths:
                continue
            related_content = read_repo_file(repo_path, path)
            if not related_content:
                continue
            clipped = related_content[:4000]
            context_paths.append(path)
            sections.append(f"\nRelated file: {path}\n{clipped}")
            total_chars += len(clipped)
            added_related += 1

        if total_chars >= max_total_chars:
            break

    return "\n".join(sections), context_paths


def build_graph_neighbors(snapshot: dict[str, Any]) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = {}
    for link in (snapshot.get("graph", {}) or {}).get("links", []) or []:
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        if not source or not target:
            continue
        neighbors.setdefault(source, set()).add(target)
        neighbors.setdefault(target, set()).add(source)
    return neighbors


def build_analysis_digest(snapshot: dict[str, Any]) -> str:
    """The measurements the pipeline computed, in a form that fits in any prompt.

    A question about a file still needs the numbers computed for it. Asked "which
    file has the worst hotspot score" over a file-scoped context, the model
    searched the tree, found nothing, and answered that the data did not exist -
    while eight ranked hotspots were sitting in the snapshot it was never shown.

    This is deliberately compact: the counts, the top hotspots and the findings
    that survived triage, nothing that belongs in a single file's context.
    """
    stats = snapshot.get("stats") or {}
    lines: list[str] = []

    if stats:
        measured = [
            f"{stats[key]} {label}"
            for key, label in (
                ("total_files", "files"),
                ("total_loc", "lines"),
                ("hotspot_count", "hotspots"),
                ("total_vulnerabilities", "findings"),
                ("dismissed_findings", "dismissed by the model"),
                ("vulnerable_dependencies", "dependencies with advisories"),
            )
            if stats.get(key) is not None
        ]
        if measured:
            lines.append("Analysis measurements: " + ", ".join(measured))

    hotspots = [item for item in (snapshot.get("hotspots") or [])[:5] if isinstance(item, dict)]
    if hotspots:
        lines.append("Top ranked hotspots:")
        lines.extend(
            f"- {item.get('file_path')} (score {item.get('score')}, complexity {item.get('complexity')}, "
            f"{item.get('commits', 0)} commits, {item.get('findings', 0)} findings)"
            for item in hotspots
        )

    findings = [
        item
        for item in (snapshot.get("vulnerabilities") or [])
        if isinstance(item, dict) and item.get("triage") != "dismissed"
    ]
    if findings:
        lines.append("Findings that survived triage:")
        lines.extend(
            f"- [{item.get('severity', '?')}] {item.get('rule', '?')} at {item.get('file_path')}:{item.get('line')} "
            f"({item.get('triage', 'pending')})"
            for item in findings[:8]
        )

    return "\n".join(lines)


def build_general_context(snapshot: dict[str, Any]) -> str:
    """Repository-level summary: structure, stack, findings and key manifests."""
    parts = [
        f"Repository URL: {snapshot.get('repo_url', '')}",
        f"Commit analysed: {snapshot.get('head_sha', '')[:12]}",
        f"Total files analysed: {len(snapshot.get('files', []))}",
    ]

    architecture = snapshot.get("architecture") or {}
    if architecture:
        parts.append(f"Architecture: {architecture.get('pattern', '')}")
        if architecture.get("summary"):
            parts.append(f"Summary: {architecture['summary']}")
        for layer in architecture.get("layers", [])[:8]:
            parts.append(
                f"  - {layer.get('name', '')}: {layer.get('responsibility', '')} "
                f"[{', '.join(str(item) for item in (layer.get('files') or [])[:6])}]"
            )

    for label, key, limit in (
        ("Tech stack", "tech_stack", 2000),
        ("Hotspots", "hotspots", 3000),
        ("Security findings", "vulnerabilities", 3000),
        ("Git history", "churn", 800),
    ):
        payload = snapshot.get(key) or {}
        if payload:
            parts.append(f"{label}: {json.dumps(payload)[:limit]}")

    manifests = snapshot.get("dependency_manifests") or []
    if manifests:
        for manifest in manifests[:4]:
            dependencies = manifest.get("dependencies", [])[:40]
            parts.append(
                f"Dependencies from {manifest.get('file_path')} ({manifest.get('ecosystem', 'unknown')}): "
                + ", ".join(
                    f"{item.get('name')}{item.get('version', '')}".strip() for item in dependencies[:40]
                )[:2000]
            )

    for file_info in (snapshot.get("special_files", []) or [])[:5]:
        parts.append(f"\nFile: {file_info.get('file_path')}\n{str(file_info.get('content', ''))[:3000]}")

    top_files = (snapshot.get("files", []) or [])[:12]
    if top_files:
        parts.append("\nRepository structure:")
        parts.extend(
            f"- {file.get('file_path')} ({file.get('language')}, complexity {file.get('complexity', 0)}, "
            f"{file.get('loc', 0)} lines)"
            for file in top_files
        )

    return "\n".join(parts)
