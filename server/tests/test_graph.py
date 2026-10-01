"""The dependency graph: do imports resolve to real files, or to nothing?

The regression that matters here is silence. A resolver that quietly fails looks
exactly like a resolver that found nothing to resolve, so the TypeScript
NodeNext case is pinned explicitly.
"""

from __future__ import annotations

import pytest

from app.core.graph import GraphBuilder, _finding_count, _norm


def files(*entries: tuple[str, list]) -> list[dict]:
    """Fixture files.

    The specifiers here are what the parser actually emits - bare module paths,
    not import statements. The parser splits `import { x } from "./x.js"` into the
    specifier `./x.js` before the graph sees it, so feeding statements in would
    be testing a shape the pipeline never produces.
    """
    return [
        {
            "file_path": path,
            "language": path.rsplit(".", 1)[-1],
            "complexity": 1,
            "loc": 10,
            "imports": list(imports),
            "vulnerabilities": [],
        }
        for path, imports in entries
    ]


def edge_set(entries: list[dict]) -> set[tuple[str, str]]:
    graph = GraphBuilder("", entries).build()
    nodes = graph.nodes.values() if isinstance(graph.nodes, dict) else graph.nodes
    links = {(link["source"], link["target"]) for link in graph.links}
    assert len(nodes) == len(entries)
    return links


def test_python_relative_imports_resolve():
    entries = files(
        ("pkg/__init__.py", []),
        ("pkg/core.py", []),
        ("pkg/api.py", [".core", "."]),
    )
    # `.` is the package itself, which is `__init__.py` - the one spelling a
    # resolver that only tries `index.js` gets wrong.
    assert edge_set(entries) == {
        ("pkg/api.py", "pkg/core.py"),
        ("pkg/api.py", "pkg/__init__.py"),
    }


def test_python_relative_import_climbs_out_of_a_subpackage():
    entries = files(
        ("pkg/__init__.py", []),
        ("pkg/thing.py", []),
        ("pkg/sub/__init__.py", []),
        ("pkg/sub/inner.py", ["..thing"]),
    )
    assert ("pkg/sub/inner.py", "pkg/thing.py") in edge_set(entries)


def test_typescript_emitted_js_extension_resolves_to_the_ts_source():
    """`from "./parse.js"` names `parse.ts` in the repository.

    NodeNext and bundler resolutions both require the emitted extension in the
    specifier. Missing this swap makes every TypeScript import look external,
    which is what made a 226-file repository report two edges.
    """
    entries = files(
        ("src/parse.ts", []),
        ("src/loader.ts", ["./parse.js"]),
    )
    assert ("src/loader.ts", "src/parse.ts") in edge_set(entries)


@pytest.mark.parametrize(
    ("emitted", "authored"),
    [("./x.js", "x.ts"), ("./x.jsx", "x.tsx"), ("./x.mjs", "x.mts"), ("./x.cjs", "x.cts")],
)
def test_every_emitted_to_source_extension_pair(emitted: str, authored: str):
    entries = files((f"src/{authored}", []), (f"src/main.ts", [emitted]))
    assert (f"src/main.ts", f"src/{authored}") in edge_set(entries)


def test_directory_import_resolves_to_its_index_file():
    entries = files(("src/lib/index.ts", []), ("src/main.ts", ["./lib"]))
    assert ("src/main.ts", "src/lib/index.ts") in edge_set(entries)


def test_bare_package_specifiers_stay_external():
    entries = files(("src/main.ts", ["express", "node:fs"]))
    graph = GraphBuilder("", entries).build()
    assert graph.links == []
    node = next(iter(graph.nodes.values()))
    assert "express" in node.external_imports
    assert "node:fs" in node.external_imports


def test_relative_escaping_the_root_resolves_to_nothing():
    entries = files(("src/main.ts", ["../../../etc/passwd"]))
    graph = GraphBuilder("", entries).build()
    assert graph.links == []


def test_imports_are_recorded_once_per_pair():
    entries = files(
        ("src/a.ts", ["./b.js", "./b.js"]),
        ("src/b.ts", []),
    )
    assert len(edge_set(entries)) == 1


def test_graph_survives_a_serialised_snapshot_shape():
    """Snapshots store a finding *count* where the parser stores a list.

    Rebuilding a graph from stored data is a normal operation - the reload path
    does it - so both shapes have to be readable.
    """
    entries = [
        {"file_path": "a.py", "language": "python", "imports": [], "vulnerabilities": 3},
        {"file_path": "b.py", "language": "python", "imports": ["a"], "vulnerabilities": []},
    ]
    graph = GraphBuilder("", entries).build()
    assert len(graph.links) == 1
    assert _finding_count(entries[0]) == 3
    assert _finding_count(entries[1]) == 0


def test_broken_import_specifiers_are_skipped_not_fatal():
    entries = files(("a.py", ["b", 42, None, "", "a"]), ("b.py", []))
    graph = GraphBuilder("", entries).build()
    # `b` is a sibling module; `42`, `None` and "" cannot resolve to anything.
    assert ("a.py", "b.py") in {(link["source"], link["target"]) for link in graph.links}


def test_paths_are_normalised_consistently():
    assert _norm("./src/a.py") == "src/a.py"
    assert _norm("src/./a.py") == "src/a.py"
    assert _norm("src/lib/../a.py") == "src/a.py"
    assert _norm("../outside.py") == ""
    assert _norm("") == ""


def test_metrics_report_real_numbers():
    entries = files(("a.py", ["b"]), ("b.py", []), ("c.py", ["b"]))
    graph = GraphBuilder("", entries).build()
    metrics = graph.metrics()
    assert metrics["file_count"] == 3
    assert metrics["link_count"] == 2