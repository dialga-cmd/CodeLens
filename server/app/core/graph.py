import os
from typing import List, Dict, Any

class GraphBuilder:
    def __init__(self, repo_path: str, analyzed_files: List[Dict[str, Any]]):
        self.repo_path = repo_path
        self.files = analyzed_files
        self.path_map = {f["file_path"]: f for f in analyzed_files}
        self.module_map = self._build_module_map()

    def _build_module_map(self) -> Dict[str, str]:
        module_map = {}
        for f in self.files:
            path = f["file_path"]
            stem = os.path.splitext(path)[0]
            module_name = stem.replace(os.sep, ".").replace("/", ".")
            module_map[module_name] = path
            module_map[os.path.basename(stem)] = path
            module_map[path] = path
        return module_map

    def build_graph(self) -> Dict[str, Any]:
        nodes = []
        links = []

        for f in self.files:
            nodes.append({
                "id": f["file_path"],
                "name": os.path.basename(f["file_path"]),
                "language": f["language"],
                "complexity": f["complexity"],
                "vulnerabilities": len(f.get("vulnerabilities", [])),
                "val": max(2, min(f["complexity"], 20)),
            })

            for imp in f["imports"]:
                target_path = self._resolve_import(imp, f["file_path"])
                if target_path and target_path in self.path_map:
                    links.append({
                        "source": f["file_path"],
                        "target": target_path,
                    })

        return {"nodes": nodes, "links": links}

    def _resolve_import(self, import_str: str, current_file: str) -> str:
        normalized = import_str.strip().strip("'\"")
        candidates = [normalized]

        if normalized.startswith("."):
            current_dir = os.path.dirname(current_file)
            relative = normalized.replace(".", "/").lstrip("/")
            candidates.extend([
                os.path.normpath(os.path.join(current_dir, relative)),
                os.path.normpath(os.path.join(current_dir, relative, "index")),
            ])
        elif normalized.startswith("/"):
            candidates.append(normalized.lstrip("/"))
        else:
            candidates.extend([
                normalized.replace("/", "."),
                normalized.replace(".", "/"),
            ])

        extensions = ["", ".py", ".js", ".jsx", ".ts", ".tsx"]
        for candidate in candidates:
            if candidate in self.module_map:
                return self.module_map[candidate]

            for ext in extensions:
                path_candidate = f"{candidate}{ext}"
                if path_candidate in self.path_map:
                    return path_candidate

        return None
