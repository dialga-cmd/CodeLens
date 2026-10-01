"""File-level dependency graph.

Answers two questions the rest of the app depends on:

* which files import which other files, so the 3D view and the Fix Advisor can
  show the neighbourhood of a change;
* how connected a file is (fan-in / fan-out), which is a hotspot signal long
  before any model is involved.

Resolution is deliberately conservative: a link is only created when it points
at a file that exists in the snapshot. External packages (npm, PyPI) are
recorded separately as unresolvable imports, because a graph full of invented
edges is worse than a small honest one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

SOURCE_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".rb", ".php", ".cs", ".kt", ".lua")

# What a TypeScript/JS specifier says vs. what the file is called in the
# repository. NodeNext and bundler resolutions both require the emitted
# extension in the import, which is not the extension on disk.
_EMITTED_TO_SOURCE_EXTENSION = {
    ".js": ".ts",
    ".jsx": ".tsx",
    ".mjs": ".mts",
    ".cjs": ".cts",
}

# Import prefixes that are always external, never a file in the repository.
EXTERNAL_PREFIXES = (
    "node:", "http://", "https://", "data:", "file:", "fs", "path", "os",
    "react", "next", "vue", "svelte", "angular", "lodash", "axios", "express",
)


def _finding_count(file: dict[str, Any]) -> int:
    """How many findings belong to one file.

    A file carries the full list when it comes straight from the parser and only
    a count once it has been serialised into a snapshot, so both shapes have to
    be read rather than assuming one.
    """
    raw = file.get("vulnerabilities")
    if isinstance(raw, (list, tuple)):
        return len(raw)
    if isinstance(raw, int):
        return raw
    count = file.get("finding_count")
    return count if isinstance(count, int) else 0


def _norm(path: str) -> str:
    """Normalise a repository-relative path to forward slashes without ``./``.

    ``normpath`` is what turns ``source/./utilities.js`` into the same key the
    walk produced, and it collapses ``a/b/../c`` the way the filesystem would.
    A path that climbs above the root becomes empty: there is no such file.
    """
    cleaned = (path or "").replace(os.sep, "/").strip()
    if not cleaned:
        return ""
    cleaned = os.path.normpath(cleaned).replace(os.sep, "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.strip("/")
    return "" if cleaned in {"", ".", ".."} or cleaned.startswith("../") else cleaned


@dataclass
class GraphNode:
    id: str
    name: str
    language: str
    complexity: int = 0
    loc: int = 0
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    external_imports: list[str] = field(default_factory=list)
    finding_count: int = 0

    @property
    def fan_in(self) -> int:
        return len(self.dependents)

    @property
    def fan_out(self) -> int:
        return len(self.dependencies)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.id,
            "language": self.language,
            "complexity": self.complexity,
            "loc": self.loc,
            "fan_in": self.fan_in,
            "fan_out": self.fan_out,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "external_imports": self.external_imports[:20],
            "finding_count": self.finding_count,
            "val": self.size_value(),
        }

    def size_value(self) -> int:
        """Node radius for the 3D view: bigger when a file is more entangled."""
        score = self.complexity * 0.6 + self.fan_in * 1.2 + self.fan_out * 0.4 + self.finding_count * 2
        return int(max(2, min(24, round(2 + score))))


class DependencyGraph:
    """An immutable view of the resolved import graph."""

    def __init__(self, nodes: dict[str, GraphNode], links: list[dict[str, str]]) -> None:
        self.nodes = nodes
        self.links = links

    def dependencies_of(self, file_path: str) -> list[str]:
        node = self.nodes.get(_norm(file_path))
        return list(node.dependencies) if node else []

    def dependents_of(self, file_path: str) -> list[str]:
        node = self.nodes.get(_norm(file_path))
        return list(node.dependents) if node else []

    def neighbourhood(self, file_path: str, depth: int = 1) -> list[str]:
        """Files within ``depth`` hops, used to give the Fix Advisor local context."""
        seen: set[str] = set()
        frontier = {_norm(file_path)}
        while frontier and depth > 0:
            neighbours: set[str] = set()
            for path in frontier:
                node = self.nodes.get(path)
                if not node:
                    continue
                neighbours.update(node.dependencies)
                neighbours.update(node.dependents)
            seen.update(neighbours)
            frontier = neighbours - seen
            depth -= 1
        seen.discard(_norm(file_path))
        return sorted(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "links": list(self.links),
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "file_count": len(self.nodes),
            "link_count": len(self.links),
            "external_import_count": sum(len(node.external_imports) for node in self.nodes.values()),
        }


class GraphBuilder:
    """Resolve the imports of every analysed file into a graph."""

    def __init__(self, repo_path: str, analyzed_files: Sequence[dict[str, Any]]) -> None:
        self.repo_path = repo_path
        self.files = [file for file in analyzed_files if file.get("file_path")]
        self.paths = {_norm(str(file["file_path"])) for file in self.files}
        self.by_stem: dict[str, list[str]] = {}
        for path in sorted(self.paths):
            stem = os.path.splitext(path)[0]
            self.by_stem.setdefault(stem, []).append(path)
            self.by_stem.setdefault(os.path.basename(stem), []).append(path)
        # Longest paths first, so a nested module wins over a shallow name clash.
        self.sorted_paths = sorted(self.paths, key=lambda path: (-path.count("/"), path))

    # -- entry point ------------------------------------------------------- #

    def build(self) -> DependencyGraph:
        nodes: dict[str, GraphNode] = {}
        for file in self.files:
            path = _norm(str(file["file_path"]))
            nodes[path] = GraphNode(
                id=path,
                name=os.path.basename(path),
                language=str(file.get("language", "unknown")),
                complexity=int(file.get("complexity", 0) or 0),
                loc=int(file.get("loc", 0) or 0),
                finding_count=_finding_count(file),
            )

        links: list[dict[str, str]] = []
        seen_links: set[tuple[str, str]] = set()

        for file in self.files:
            source = _norm(str(file["file_path"]))
            if source not in nodes:
                continue
            node = nodes[source]
            imports = file.get("imports") or file.get("import_source_imports") or []
            if not isinstance(imports, (list, tuple, set)):
                imports = []
            for specifier in imports:
                try:
                    target = self._resolve(str(specifier), source)
                except Exception:
                    continue
                if target and target != source and target in nodes:
                    key = (source, target)
                    if key in seen_links:
                        continue
                    seen_links.add(key)
                    node.dependencies.append(target)
                    if target in nodes:
                        nodes[target].dependents.append(source)
                    links.append({"source": source, "target": target})
                elif not target:
                    node.external_imports.append(str(specifier))

        for node in nodes.values():
            node.dependencies.sort()
            node.dependents.sort()
            node.external_imports = sorted(dict.fromkeys(node.external_imports))

        return DependencyGraph(nodes, links)

    # -- resolution -------------------------------------------------------- #

    def _first_existing(self, candidates: Iterable[str]) -> str | None:
        for candidate in candidates:
            if not candidate:
                continue
            normalized = _norm(candidate)
            if normalized in self.paths:
                return normalized
        return None

    def _with_extensions(self, base: str) -> list[str]:
        stem = base.rstrip("/")
        candidates = [f"{stem}{ext}" for ext in SOURCE_EXTENSIONS]
        # Python names a package's module `__init__.py`; JS names it `index.js`.
        # Both spellings are needed, because both are what real repositories use.
        candidates += [f"{stem}/__init__.py"]
        candidates += [f"{stem}/index{ext}" for ext in SOURCE_EXTENSIONS]

        # TypeScript's NodeNext convention writes the *output* extension in the
        # specifier: `import { x } from "./external.js"` names a file that is
        # `external.ts` in the repository. Without this swap every TypeScript
        # import looks external and the graph comes out empty - which is exactly
        # what a monorepo written this way should not produce.
        for emitted, authored in _EMITTED_TO_SOURCE_EXTENSION.items():
            if stem.endswith(emitted):
                trimmed = stem[: -len(emitted)]
                candidates = (
                    [f"{trimmed}{authored}", f"{trimmed}/index{authored}"] + candidates
                )

        return candidates

    def _suffix_lookup(self, dotted_or_slashed: str) -> str | None:
        """Match a package-style import against repository paths by suffix."""
        needle = _norm(dotted_or_slashed).replace(".", "/")
        candidates = self._with_extensions(needle)
        for candidate in candidates:
            for path in self.sorted_paths:
                if path == candidate or path.endswith("/" + candidate):
                    return path
        return None

    def _resolve(self, specifier: str, source_path: str) -> str | None:
        specifier = specifier.strip()
        if not specifier:
            return None

        # `from . import helpers` is captured as a whole statement by the regex
        # fallback; the module that matters is the trailing name.
        tail = ""
        if " import " in specifier:
            head, _, tail = specifier.partition(" import ")
            specifier = head.strip()
            tail = tail.strip().split(",")[0].strip()

        if specifier.startswith(EXTERNAL_PREFIXES) and not specifier.startswith((".", "/")):
            return None

        source_dir = os.path.dirname(source_path)

        # Python relative import: one leading dot means the current package.
        if specifier.startswith(".") and not specifier.startswith(("./", "../")):
            return self._resolve_python_relative(specifier, source_path, source_dir, tail)

        # Path-relative import used by JS/TS and Go-style relative paths.
        if specifier.startswith("./") or specifier.startswith("../"):
            joined = _norm(os.path.join(source_dir, specifier))
            return self._first_existing(self._with_extensions(joined)) or self._first_existing([joined])

        if specifier.startswith("/"):
            # Python 2 style implicit relative import; treat as repo-root relative.
            return self._first_existing(self._with_extensions(specifier[1:])) or self._first_existing([specifier[1:]])

        # Bare specifier: could be a sibling module, a package path, or external.
        dotted = specifier if specifier.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs")) else specifier
        local_first = self._first_existing(
            self._with_extensions(os.path.join(source_dir, dotted)) + self._with_extensions(dotted)
        )
        if local_first:
            return local_first

        # A single-word specifier may name a sibling module (`from helpers import x`
        # next to helpers.py). A dotted or scoped specifier must match the whole
        # path, otherwise `fastapi.security` would be "found" in any repo that has
        # a security.py lying around.
        if "." not in specifier and "/" not in specifier:
            for path in self.sorted_paths:
                if os.path.splitext(os.path.basename(path))[0] == specifier:
                    if os.path.dirname(path) in {"", source_dir}:
                        return path

        return self._suffix_lookup(specifier)

    def _resolve_python_relative(self, specifier: str, source_path: str, source_dir: str, tail: str = "") -> str | None:
        level = len(specifier) - len(specifier.lstrip("."))
        remainder = specifier[level:].replace(".", "/")

        base = source_dir
        # `__init__.py` lives in the package directory, so a relative import
        # inside it is relative to that same directory, not to its parent.
        # Climbing past the repository root simply means "the repo root".
        for _ in range(max(0, level - 1)):
            if not base:
                break
            base = os.path.dirname(base)

        if not remainder:
            # `from . import helpers` / `from .. import thing`
            if tail:
                resolved = self._first_existing(self._with_extensions(_norm(os.path.join(base, tail))))
                if resolved:
                    return resolved
            return self._first_existing(self._with_extensions(base)) or self._first_existing([base])

        joined = _norm(os.path.join(base, remainder))
        if tail:
            # `from ..pkg import thing` targets the pkg/thing module.
            joined = _norm(os.path.join(joined, tail))
        return self._first_existing(self._with_extensions(joined)) or self._first_existing([joined])
