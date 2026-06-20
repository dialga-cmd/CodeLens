from tree_sitter_languages import get_language, get_parser
from typing import List, Dict, Any


class CodeParser:
    def __init__(self, language_name: str):
        self.language_name = language_name
        self.language = get_language(language_name)
        self.parser = get_parser(language_name)

    def parse_file(self, file_path: str) -> Dict[str, Any]:
        with open(file_path, "r", errors="ignore") as f:
            content = f.read()

        tree = self.parser.parse(bytes(content, "utf8"))
        root_node = tree.root_node

        results = {
            "file_path": file_path,
            "language": self.language_name,
            "functions": self._extract_functions(root_node, content),
            "classes": self._extract_classes(root_node, content),
            "imports": self._extract_imports(root_node, content),
            "complexity": self._calculate_complexity(root_node),
        }
        return results

    def _node_text(self, node, content: str) -> str:
        return content[node.start_byte:node.end_byte]

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _named_child_text(self, node, field_name: str, content: str) -> str:
        child = node.child_by_field_name(field_name)
        if child is None:
            return ""
        return self._node_text(child, content)

    def _function_name_from_variable(self, node, content: str) -> str:
        if node.type != "variable_declarator":
            return ""

        value = node.child_by_field_name("value")
        if value is None or value.type not in {"arrow_function", "function", "function_expression"}:
            return ""

        name = node.child_by_field_name("name")
        if name is None:
            return ""
        return self._node_text(name, content)

    def _extract_functions(self, root_node, content: str) -> List[Dict[str, Any]]:
        functions = []
        function_nodes = {
            "function_definition",
            "function_declaration",
            "method_definition",
            "generator_function_declaration",
        }

        for node in self._walk(root_node):
            name = ""
            if node.type in function_nodes:
                name = self._named_child_text(node, "name", content)
            elif node.type == "variable_declarator":
                name = self._function_name_from_variable(node, content)

            if name:
                functions.append({
                    "name": name,
                    "start_line": node.start_point[0],
                    "end_line": node.end_point[0],
                })

        return functions

    def _extract_classes(self, root_node, content: str) -> List[Dict[str, Any]]:
        classes = []
        class_nodes = {"class_definition", "class_declaration"}

        for node in self._walk(root_node):
            if node.type not in class_nodes:
                continue

            name = self._named_child_text(node, "name", content)
            if name:
                classes.append({
                    "name": name,
                    "start_line": node.start_point[0],
                    "end_line": node.end_point[0],
                })

        return classes

    def _extract_imports(self, root_node, content: str) -> List[str]:
        imports = []

        for node in self._walk(root_node):
            if self.language_name == "python":
                if node.type == "import_statement":
                    imports.extend(self._extract_python_import_names(node, content))
                elif node.type == "import_from_statement":
                    module = self._extract_python_from_module(node, content)
                    if module:
                        imports.append(module)

            if self.language_name in {"javascript", "typescript", "tsx"}:
                if node.type == "import_statement":
                    source = self._first_string_child(node, content)
                    if source:
                        imports.append(source)
                elif node.type == "call_expression":
                    function_name = self._named_child_text(node, "function", content)
                    if function_name == "require":
                        source = self._first_string_child(node, content)
                        if source:
                            imports.append(source)

        return sorted(set(imports))

    def _extract_python_import_names(self, node, content: str) -> List[str]:
        text = self._node_text(node, content).strip()
        if not text.startswith("import "):
            return []

        names = []
        for item in text[len("import "):].split(","):
            name = item.strip().split(" as ")[0].strip()
            if name:
                names.append(name)
        return names

    def _extract_python_from_module(self, node, content: str) -> str:
        for child in node.children:
            if child.type in {"dotted_name", "relative_import"}:
                return self._node_text(child, content).strip()
        return ""

    def _first_string_child(self, node, content: str) -> str:
        for child in self._walk(node):
            if child.type in {"string", "string_fragment"}:
                return self._node_text(child, content).strip("'\"")
        return ""

    def _calculate_complexity(self, root_node) -> int:
        branching_nodes = {
            "if_statement", "for_statement", "while_statement",
            "conditional_expression", "and", "or", "case_clause",
            "for_in_statement", "try_statement", "catch_clause",
        }

        count = 0
        for node in self._walk(root_node):
            if node.type in branching_nodes:
                count += 1

        return 1 + count
