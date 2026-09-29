"""Tree-sitter parsing: real structural facts about a source file.

The parser produces the metrics the rest of CodeLens depends on, so it has to be
honest: if a grammar is missing or a file is malformed, it says so and lets the
caller continue with a regex fallback instead of aborting the run.

Extracted per file: cyclomatic complexity (file and per function), function and
class inventories with line ranges, imports, and line counts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterator, Sequence

from tree_sitter_languages import get_language, get_parser

# Grammars shipped by tree-sitter-languages 1.10.2 that we rely on.
SUPPORTED_LANGUAGES = {
    "bash",
    "c",
    "c_sharp",
    "cpp",
    "elixir",
    "go",
    "haskell",
    "java",
    "javascript",
    "kotlin",
    "lua",
    "ocaml",
    "perl",
    "php",
    "python",
    "r",
    "ruby",
    "rust",
    "scala",
    "tsx",
    "typescript",
}

# File extension -> grammar name. Extensions not listed here fall back to the
# regex import extraction, so a file is never dropped for lack of a grammar.
EXTENSION_LANGUAGES = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "tsx",
    "go": "go",
    "java": "java",
    "rs": "rust",
    "c": "c",
    "h": "c",
    "cc": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "hh": "cpp",
    "rb": "ruby",
    "php": "php",
    "cs": "c_sharp",
    "sh": "bash",
    "bash": "bash",
    "kt": "kotlin",
    "kts": "kotlin",
    "scala": "scala",
    "lua": "lua",
    "ex": "elixir",
    "exs": "elixir",
    "pl": "perl",
    "r": "r",
    "jl": "python",  # no Julia grammar: keeps the file in the walk, parsed as text
}

# Node types that add one decision point to cyclomatic complexity. The union
# across grammars is deliberate: a node type simply never appears in a grammar
# that does not have it.
BRANCHING_NODES = {
    "if_statement",
    "if_expression",
    "elif_clause",
    "guard_statement",
    "for_statement",
    "for_in_statement",
    "foreach_statement",
    "while_statement",
    "do_statement",
    "case_clause",
    "switch_case",
    "when_entry",
    "catch_clause",
    "except_clause",
    "rescue",
    "conditional_expression",
    "ternary_expression",
    "boolean_operator",
}

BOOLEAN_OPERATORS = {"&&", "||", "and", "or", "AND", "OR"}

FUNCTION_NODES = {
    "function_definition",
    "function_declaration",
    "function_item",
    "function_signature_item",
    "function_expression",
    "generator_function_declaration",
    "arrow_function",
    "method_definition",
    "method_declaration",
    "constructor_declaration",
    "local_function_statement",
    "singleton_method",
    "method",
}

CLASS_NODES = {
    "class_definition",
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "struct_item",
    "enum_item",
    "trait_item",
    "impl_item",
    "type_declaration",
    "module",
}

COMMENT_NODES = {"comment", "line_comment", "block_comment"}


class UnsupportedLanguage(ValueError):
    """Raised when no Tree-sitter grammar is available for a language."""


@dataclass
class FunctionFacts:
    name: str
    start_line: int
    end_line: int
    complexity: int
    lines: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "complexity": self.complexity,
            "lines": self.lines,
        }


@dataclass
class FileFacts:
    """Everything the analysis pipeline needs to know about one file."""

    file_path: str
    language: str
    complexity: int = 0
    loc: int = 0
    comment_lines: int = 0
    functions: list[FunctionFacts] = field(default_factory=list)
    classes: list[dict[str, Any]] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    parsed_with: str = "tree-sitter"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "complexity": self.complexity,
            "loc": self.loc,
            "comment_lines": self.comment_lines,
            "functions": [function.to_dict() for function in self.functions],
            "classes": self.classes,
            "imports": self.imports,
            "parsed_with": self.parsed_with,
        }


class CodeParser:
    """A Tree-sitter parser bound to one language."""

    def __init__(self, language_name: str) -> None:
        if language_name not in SUPPORTED_LANGUAGES:
            raise UnsupportedLanguage(f"no grammar for language '{language_name}'")
        self.language_name = language_name
        self.language = get_language(language_name)
        self.parser = get_parser(language_name)

    # -- traversal --------------------------------------------------------- #

    def _walk(self, root) -> Iterator[Any]:
        """Iterative depth-first walk. Iterative so deep files cannot blow the stack."""
        stack = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def _node_text(self, node, content: str) -> str:
        return content[node.start_byte : node.end_byte]

    def _field_text(self, node, field_name: str, content: str) -> str:
        child = node.child_by_field_name(field_name)
        return self._node_text(child, content) if child is not None else ""

    # -- complexity -------------------------------------------------------- #

    def _decision_points(self, node) -> int:
        count = 0
        for child in self._walk(node):
            if child.type in BRANCHING_NODES:
                count += 1
                continue
            # C-like grammars express && and || as binary_expression operators.
            if child.type == "binary_expression":
                operator = child.child_by_field_name("operator")
                if operator is not None and self._node_text(operator, "") in BOOLEAN_OPERATORS:
                    count += 1
        return count

    def _complexity_of(self, node) -> int:
        return 1 + self._decision_points(node)

    # -- names ------------------------------------------------------------- #

    def _function_name(self, node, content: str) -> str:
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is None or value.type not in {"arrow_function", "function", "function_expression"}:
                return ""
            return self._field_text(node, "name", content)

        if node.type == "pair" and node.child_by_field_name("key") is not None:
            # object-literal method: { handler() {} }
            pass

        return self._field_text(node, "name", content)

    def _line_span(self, node) -> tuple[int, int]:
        # Tree-sitter rows are 0-based; everything CodeLens shows is 1-based.
        return node.start_point[0] + 1, node.end_point[0] + 1

    # -- extraction -------------------------------------------------------- #

    def parse_source(self, content: str, file_path: str = "") -> FileFacts:
        """Parse in-memory source. Never raises for malformed input."""
        facts = FileFacts(
            file_path=file_path,
            language=self.language_name,
            loc=len(content.splitlines()),
        )
        try:
            tree = self.parser.parse(bytes(content, "utf8"))
        except Exception as error:  # noqa: BLE001 - reported, not raised
            facts.parsed_with = "unavailable"
            facts.error = f"parse failed: {error}"
            return facts

        root = tree.root_node
        if root is None:
            facts.parsed_with = "unavailable"
            facts.error = "empty syntax tree"
            return facts

        facts.complexity = self._complexity_of(root)

        for node in self._walk(root):
            if node.type in COMMENT_NODES:
                facts.comment_lines += 1
                continue

            if node.type in FUNCTION_NODES:
                name = self._function_name(node, content)
                if not name:
                    continue
                start_line, end_line = self._line_span(node)
                facts.functions.append(
                    FunctionFacts(
                        name=name,
                        start_line=start_line,
                        end_line=end_line,
                        complexity=self._complexity_of(node),
                        lines=end_line - start_line + 1,
                    )
                )
                continue

            if node.type in CLASS_NODES:
                name = self._field_text(node, "name", content) or self._field_text(node, "type", content)
                if not name:
                    continue
                start_line, end_line = self._line_span(node)
                facts.classes.append({"name": name, "start_line": start_line, "end_line": end_line})

        facts.imports = self._extract_imports(root, content)
        return facts

    def parse_file(self, file_path: str) -> FileFacts:
        with open(file_path, "rb") as handle:
            content = handle.read().decode("utf-8", errors="ignore")
        return self.parse_source(content, file_path=file_path)

    def _extract_imports(self, root, content: str) -> list[str]:
        imports: list[str] = []

        for node in self._walk(root):
            node_type = node.type
            if node_type in {"import_statement", "import_from_statement"}:
                module = self._field_text(node, "module_name", content) or self._field_text(node, "source", content)
                if module:
                    imports.append(self._clean_specifier(module))
                # Python: `import a.b, c` has repeated name fields, no module_name.
                for name_field in self._dotted_names(node, content):
                    imports.append(name_field)
            elif node_type in {"import_declaration", "use_declaration"}:
                source = self._field_text(node, "source", content)
                if source:
                    imports.append(self._clean_specifier(source))
            elif node_type == "call_expression":
                function = self._field_text(node, "function", content)
                if function in {"require", "import"}:
                    source = self._first_string(node, content)
                    if source:
                        imports.append(source)
            elif node_type in {"using_directive", "preproc_include"}:
                source = self._first_string(node, content) or self._field_text(node, "path", content)
                if source:
                    imports.append(source)

        return sorted(dict.fromkeys(item for item in imports if item))

    @staticmethod
    def _clean_specifier(text: str) -> str:
        """Strip the quoting a grammar keeps around an import path."""
        return text.strip().strip("'\"").strip()

    def _dotted_names(self, node, content: str) -> list[str]:
        names: list[str] = []
        for child in node.children:
            if child.type == "dotted_name":
                names.append(self._node_text(child, content).strip())
            elif child.type == "aliased_import":
                for grandchild in child.children:
                    if grandchild.type == "dotted_name":
                        names.append(self._node_text(grandchild, content).strip())
        return names

    def _first_string(self, node, content: str) -> str:
        for child in self._walk(node):
            if child.type in {"string", "string_literal", "interpreted_string_literal", "raw_string_literal", "system_lib_string"}:
                return self._node_text(child, content).strip("'\"`<>")
        return ""


# --------------------------------------------------------------------------- #
# helpers used by the analyzer
# --------------------------------------------------------------------------- #

# Machine-written files. They are usually the largest files in a repository and
# the least useful to a human or a model: they inflate complexity, churn and
# size without describing anything a developer wrote.
GENERATED_SUFFIXES = (
    ".d.ts", ".min.js", ".min.css", ".bundle.js", ".generated.ts", ".generated.js",
    "_pb2.py", "_pb2_grpc.py", ".pb.go", ".g.dart", ".snap", ".map", ".lock.json",
)
GENERATED_MARKERS = ("/vendor/", "/third_party/", "/thirdparty/", "/generated/", "/__generated__/")


def is_generated_path(file_name: str, relative_path: str = "") -> bool:
    """True for declarations, bundles, vendored trees and generated sources."""
    lowered = (file_name or "").lower()
    if lowered.endswith(GENERATED_SUFFIXES):
        return True
    probe = f"/{(relative_path or file_name).lower()}"
    return any(marker in probe for marker in GENERATED_MARKERS)


def language_for_path(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    return EXTENSION_LANGUAGES.get(ext, "")


@lru_cache(maxsize=None)
def get_code_parser(language_name: str) -> CodeParser | None:
    """Return a cached parser, or ``None`` when the grammar is unavailable."""
    try:
        return CodeParser(language_name)
    except (UnsupportedLanguage, Exception):  # noqa: BLE001 - grammar may be missing at runtime
        return None


def parse_source_safely(language_name: str, content: str, file_path: str = "") -> FileFacts | None:
    """Parse a file, returning ``None`` instead of raising.

    Used per file in the analysis loop so one unparseable file cannot fail a run.
    """
    parser = get_code_parser(language_name) if language_name else None
    if parser is None:
        return None
    try:
        facts = parser.parse_source(content, file_path=file_path)
    except Exception:  # noqa: BLE001 - defensive: parser.parse_source already guards
        return None
    if facts.parsed_with != "tree-sitter":
        return None
    return facts


def extract_imports_with_regex(content: str, ext: str) -> list[str]:
    """Regex import extraction, used when no grammar is available."""
    import re

    imports: list[str] = []
    if ext == "py":
        pattern = r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"
        for match in re.finditer(pattern, content, re.MULTILINE):
            imports.extend(group for group in match.groups() if group)
    elif ext in {"js", "jsx", "ts", "tsx", "mjs", "cjs"}:
        for pattern in (
            r"import\s+.*?from\s+['\"]([^'\"]+)['\"]",
            r"import\s+['\"]([^'\"]+)['\"]",
            r"require\(\s*['\"]([^'\"]+)['\"]\s*\)",
        ):
            imports.extend(re.findall(pattern, content))
    elif ext == "go":
        imports.extend(re.findall(r"^\s*import\s+\"([^\"]+)\"", content, re.MULTILINE))
        block = re.search(r"import\s*\((.*?)\)", content, re.DOTALL)
        if block:
            imports.extend(re.findall(r"\"([^\"]+)\"", block.group(1)))
    elif ext in {"java", "kt", "kts", "scala"}:
        imports.extend(re.findall(r"^import\s+(?:static\s+)?([\w.]+)", content, re.MULTILINE))
    elif ext == "rs":
        imports.extend(re.findall(r"^\s*(?:pub\s+)?use\s+([\w:]+)", content, re.MULTILINE))
    return list(dict.fromkeys(item for item in imports if item))
