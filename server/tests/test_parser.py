"""The parser: does it read real syntax, or only look like it does.

These are the tests that would catch a silent downgrade - a grammar that stops
loading, a node type renamed upstream, a complexity count that quietly becomes
"number of lines". They use the real tree-sitter bindings, not a stub.
"""

from __future__ import annotations

import pytest

from app.core.parser import (
    BRANCHING_NODES,
    language_for_path,
    parse_source_safely,
)

PYTHON_SAMPLE = '''
import os
from .helpers import shared


def classify(value, strict=False):
    """Branch a lot, so complexity is worth asserting on."""
    if value is None:
        return "none"
    elif isinstance(value, dict):
        for key, item in value.items():
            if item and key != "skip":
                if strict:
                    return key
                else:
                    continue
    elif isinstance(value, (list, tuple)):
        while value:
            value.pop()
    try:
        return os.environ.get("MODE", "default")
    except KeyError:
        return None
    finally:
        pass


class Registry:
    def __init__(self):
        self.items = {}

    def add(self, key, value):
        if key in self.items:
            raise ValueError(key)
        self.items[key] = value
        return self
'''

TYPESCRIPT_SAMPLE = """
import { readFile } from "node:fs";
import { parse } from "./parse.js";

export interface Options { retries: number }

export class Loader {
  private cache = new Map<string, string>();

  async load(path: string, options?: Options): Promise<string> {
    if (this.cache.has(path)) {
      return this.cache.get(path)!;
    }
    const body = readFile(path, "utf8");
    this.cache.set(path, body);
    return parse(body, options?.retries ?? 1);
  }
}
"""


def test_python_functions_and_classes_come_from_the_ast():
    facts = parse_source_safely("python", PYTHON_SAMPLE, "sample.py")
    assert facts is not None, "python grammar must load"

    names = {function.name for function in facts.functions}
    assert "classify" in names
    assert "add" in names, "a method inside a class is still a function definition"

    classes = {str(item.get("name", "")) for item in facts.classes}
    assert "Registry" in classes, facts.classes


def test_complexity_counts_decision_points_not_lines():
    facts = parse_source_safely("python", PYTHON_SAMPLE, "sample.py")
    assert facts is not None

    classify = next(f for f in facts.functions if f.name == "classify")
    # if / elif / for / if / else / while / ternary / except / finally is well
    # over the branch count; anything near 1 means the node walk stopped working.
    assert classify.complexity >= 8, f"expected a branch count, got {classify.complexity}"
    assert classify.complexity < len(PYTHON_SAMPLE.splitlines()), "not a line count"


def test_imports_include_relative_specifiers_verbatim():
    facts = parse_source_safely("python", PYTHON_SAMPLE, "sample.py")
    assert facts is not None
    assert any(".helpers" in specifier for specifier in facts.imports), facts.imports
    assert any(specifier == "os" for specifier in facts.imports), facts.imports


def test_typescript_imports_keep_the_emitted_js_extension():
    facts = parse_source_safely("typescript", TYPESCRIPT_SAMPLE, "loader.ts")
    assert facts is not None, "typescript grammar must load"

    specifiers = " ".join(facts.imports)
    assert "./parse.js" in specifiers, facts.imports
    assert "Loader" in {str(item.get("name", "")) for item in facts.classes}, facts.classes


def test_unsupported_language_is_reported_not_guessed():
    assert parse_source_safely("cobol", "IDENTIFICATION DIVISION.", "x.cbl") is None


def test_language_lookup_covers_the_documented_languages():
    assert language_for_path("a/b.py") == "python"
    assert language_for_path("a/b.tsx") == "tsx"
    assert language_for_path("a/b.go") == "go"
    assert language_for_path("a/b.rs") == "rust"
    assert language_for_path("a/b.unknown") == ""


def test_branching_node_table_is_not_empty():
    # If this ever empties out, complexity silently degrades to 1 everywhere.
    assert len(BRANCHING_NODES) >= 8


@pytest.mark.parametrize(
    ("path", "language"),
    [("m.py", "python"), ("m.js", "javascript"), ("m.ts", "typescript"), ("m.java", "java")],
)
def test_small_but_real_files_parse(path: str, language: str):
    source = "def f(x):\n    return x\n" if language == "python" else "function f(x) { return x; }\n"
    facts = parse_source_safely(language, source, path)
    assert facts is not None
    assert "f" in {function.name for function in facts.functions}