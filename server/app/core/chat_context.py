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
    "want", "access", "look", "see",
}

BROAD_MARKERS = (
    "all files", "related", "everything", "entire", "broad", "overview",
    "analyze", "read all", "scan", "chat", "conversation",
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
            if ext in CONTEXT_EXTENSIONS or file_name.lower() in CONTEXT_FILE_NAMES or file_name.startswith(".env"):
                collected.append(os.path.relpath(full_path, repo_path))
    return collected


def query_terms(query: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.split(r"[^a-zA-Z0-9_.-]+", (query or "").lower()):
        token = raw.strip("._-")
        if len(token) < 2 or token in STOP_WORDS or token in tokens:
            continue
        tokens.append(token)
    return tokens


def is_broad_query(query: str) -> bool:
    lowered = (query or "").lower()
    return any(marker in lowered for marker in BROAD_MARKERS)


def _score_file(file_path: str, terms: Sequence[str]) -> int:
    lowered = file_path.lower()
    basename = os.path.basename(lowered)
    dirname = os.path.dirname(lowered)
    score = 0
    for term in terms:
        if term == basename:
            score += 8
        if term in basename:
            score += 6
        if term in lowered:
            score += 4
        if term in dirname:
            score += 2
    if basename in {"readme.md", "package.json", "requirements.txt", "pyproject.toml"}:
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

    scored = [
        (score, file_info)
        for file_info in files
        if (score := _score_file(str(file_info.get("file_path", "")), terms)) > 0
    ]

    if not scored and broad:
        special = {str(item.get("file_path", "")) for item in snapshot.get("special_files", []) or []}
        scored = [(1, file_info) for file_info in files if str(file_info.get("file_path", "")) in special]

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


def build_general_context(snapshot: dict[str, Any]) -> str:
    """Repository-level summary: structure, stack, findings and key manifests."""
    parts = [
        f"Repository URL: {snapshot.get('repo_url', '')}",
        f"Total files analysed: {len(snapshot.get('files', []))}",
    ]

    for label, key, limit in (
        ("Tech stack", "tech_stack", 3000),
        ("Dependencies", "ai_dependencies", 3000),
        ("Hotspots", "hotspots", 3000),
        ("Security findings", "vulnerabilities", 3000),
    ):
        payload = snapshot.get(key) or {}
        if payload:
            parts.append(f"{label}: {json.dumps(payload)[:limit]}")

    for file_info in (snapshot.get("special_files", []) or [])[:5]:
        parts.append(f"\nFile: {file_info.get('file_path')}\n{str(file_info.get('content', ''))[:3000]}")

    top_files = (snapshot.get("files", []) or [])[:8]
    if top_files:
        parts.append("\nRepository structure:")
        parts.extend(f"- {file.get('file_path')} ({file.get('language')})" for file in top_files)

    return "\n".join(parts)
