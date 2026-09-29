"""Tools the assistant can call while answering questions about a repository.

The point of these three tools is that the model does not have to be handed the
whole repository, or be trusted to remember what it was told. It can ask for a
file, ask where something is, and ask what a file depends on, and every answer
comes from the same clone the analysis was built from.

Two properties are deliberate:

* everything is read through :mod:`app.core.chat_context`, so a tool cannot read
  a file outside the repository root, and byte budgets are enforced per call;
* a tool never raises. It returns ``{"error": ...}`` so the model can recover and
  try a different path, instead of the whole answer failing.
"""

from __future__ import annotations

import os
import re
from typing import Any, Sequence

from .chat_context import read_repo_file, resolve_repo_file

MAX_READ_BYTES = 8_000
MAX_SEARCH_HITS = 25
MAX_SEARCH_FILES = 400
MAX_LINE_LENGTH = 240

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file from the analysed repository. Use this when you need the exact source of a file "
                "rather than its metrics. Long files are truncated and say so."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repository-relative path, for example app/api.py",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search the repository for text or a regular expression and return matching lines with their "
                "file and line number. Use this before guessing where something is defined."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text or regular expression to look for"},
                    "max_results": {
                        "type": "integer",
                        "description": f"Maximum number of matching lines to return (default and cap {MAX_SEARCH_HITS})",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dependencies_of_file",
            "description": (
                "List what a file imports and what imports it, using the import graph resolved during analysis. "
                "Use this to see the blast radius of a change or to follow a request path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repository-relative path"}
                },
                "required": ["path"],
            },
        },
    },
]


class CodeTools:
    """Executes assistant tool calls against one stored analysis snapshot."""

    def __init__(self, snapshot: dict[str, Any], max_read_bytes: int = MAX_READ_BYTES) -> None:
        self.snapshot = snapshot
        self.repo_path = str(snapshot.get("repo_path", ""))
        self.max_read_bytes = max_read_bytes
        self.calls: list[dict[str, Any]] = []
        self._graph = _graph_indexes(snapshot)

    # -- dispatch ---------------------------------------------------------- #

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "read_file": self.read_file,
            "search_code": self.search_code,
            "list_dependencies_of_file": self.list_dependencies_of_file,
        }.get(name)

        if handler is None:
            return {"error": f"unknown tool: {name}"}

        try:
            outcome = handler(arguments)
        except Exception as error:  # noqa: BLE001 - a tool failure is information for the model
            outcome = {"error": str(error)[:300]}

        self.calls.append({"name": name, "arguments": arguments})
        return outcome

    # -- tools ------------------------------------------------------------- #

    def read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path", "")).strip()
        if not path:
            return {"error": "path is required"}

        content = read_repo_file(self.repo_path, path, self.max_read_bytes)
        if not content:
            return {"error": f"could not read {path}: it does not exist in the analysed clone or is outside the repository"}

        metrics = next(
            (file for file in self.snapshot.get("files", []) if str(file.get("file_path", "")) == path),
            {},
        )
        truncated = len(content) >= self.max_read_bytes
        return {
            "path": path,
            "language": metrics.get("language", ""),
            "lines": content.count("\n") + 1,
            "truncated": truncated,
            "complexity": metrics.get("complexity", 0),
            "findings": metrics.get("finding_count", 0),
            "content": content,
        }

    def search_code(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": "query is required"}
        try:
            limit = int(arguments.get("max_results", MAX_SEARCH_HITS))
        except (TypeError, ValueError):
            limit = MAX_SEARCH_HITS
        limit = max(1, min(limit, MAX_SEARCH_HITS))

        pattern = _compile(query)
        hits: list[dict[str, Any]] = []
        scanned = 0

        for file in self.snapshot.get("files", []):
            if scanned >= MAX_SEARCH_FILES or len(hits) >= limit:
                break
            path = str(file.get("file_path", ""))
            if not path:
                continue
            scanned += 1

            content = read_repo_file(self.repo_path, path, MAX_READ_BYTES)
            if not content:
                continue

            for number, line in enumerate(content.splitlines(), start=1):
                if not pattern.search(line):
                    continue
                hits.append({"path": path, "line": number, "text": line.strip()[:MAX_LINE_LENGTH]})
                if len(hits) >= limit:
                    break

        return {
            "query": query,
            "files_scanned": scanned,
            "match_count": len(hits),
            "truncated": len(hits) >= limit or scanned >= MAX_SEARCH_FILES,
            "matches": hits,
        }

    def list_dependencies_of_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path", "")).strip()
        if not path:
            return {"error": "path is required"}
        if not self._graph["paths"] or path not in self._graph["paths"]:
            return {"error": f"{path} is not in the analysed file set", "known_files": self._graph["paths"][:20]}

        return {
            "path": path,
            "imports": self._graph["imports"].get(path, []),
            "imported_by": self._graph["imports_by"].get(path, []),
            "external_imports": self._graph["external"].get(path, []),
            "fan_in": len(self._graph["imports_by"].get(path, [])),
            "fan_out": len(self._graph["imports"].get(path, [])),
        }


def _graph_indexes(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Reverse the stored graph so lookups are a dictionary read, not a scan."""
    graph = snapshot.get("graph") or {}
    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]

    imports: dict[str, list[str]] = {}
    imports_by: dict[str, list[str]] = {}
    external: dict[str, list[str]] = {}

    for node in nodes:
        path = str(node.get("id") or node.get("path") or "")
        if not path:
            continue
        imports[path] = sorted(str(value) for value in (node.get("dependencies") or []))
        imports_by[path] = sorted(str(value) for value in (node.get("dependents") or []))
        values = node.get("external_imports")
        if isinstance(values, list) and values:
            external[path] = [str(value) for value in values][:20]

    if not any(imports.values()) and not any(imports_by.values()):
        # A graph stored as bare links: rebuild the two directions from them.
        for link in graph.get("links", []):
            source = str(link.get("source", ""))
            target = str(link.get("target", ""))
            if source and target:
                imports.setdefault(source, []).append(target)
                imports_by.setdefault(target, []).append(source)
        imports = {path: sorted(set(values)) for path, values in imports.items()}
        imports_by = {path: sorted(set(values)) for path, values in imports_by.items()}

    return {
        "paths": set(imports) | set(imports_by),
        "imports": imports,
        "imports_by": imports_by,
        "external": external,
    }


def _compile(query: str) -> re.Pattern[str]:
    """Treat the query as a regular expression when it is one, else as text."""
    try:
        return re.compile(query, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(query), re.IGNORECASE)
