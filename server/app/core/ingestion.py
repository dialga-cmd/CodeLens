import os
import git
import shutil
import hashlib
import tempfile
import json
from urllib.parse import urlparse

class IngestionEngine:
    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(tempfile.gettempdir(), "codelens_repos")
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.registry_path = os.path.join(self.base_dir, "_registry.json")

    def _get_repo_path(self, repo_url: str) -> str:
        # Create a unique directory name based on the URL hash
        url_hash = hashlib.sha256(repo_url.encode()).hexdigest()[:12]
        repo_name = repo_url.split("/")[-1].replace(".git", "") or "repo"
        return os.path.join(self.base_dir, f"{repo_name}_{url_hash}")

    def _validate_repo_url(self, repo_url: str):
        parsed = urlparse(repo_url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            raise ValueError("Only https://github.com repositories are supported.")

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError("Repository URL must include an owner and repository name.")

    def clone_repo(self, repo_url: str) -> str:
        self._validate_repo_url(repo_url)
        target_path = self._get_repo_path(repo_url)
        if os.path.exists(target_path):
            shutil.rmtree(target_path)
        print(f"Cloning {repo_url} to temporary directory {target_path}...")
        git.Repo.clone_from(repo_url, target_path, depth=1)

        return target_path

    def cleanup(self, repo_path: str):
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)

    def _read_registry(self):
        if not os.path.exists(self.registry_path):
            return {}
        try:
            with open(self.registry_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _write_registry(self, payload):
        tmp_path = f"{self.registry_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, self.registry_path)

    def save_snapshot(self, repo_id: str, snapshot: dict):
        snapshot_path = os.path.join(self.base_dir, f"{repo_id}.snapshot.json")
        with open(snapshot_path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle)

        registry = self._read_registry()
        registry[repo_id] = snapshot_path
        self._write_registry(registry)

    def load_snapshot(self, repo_id: str):
        registry = self._read_registry()
        snapshot_path = registry.get(repo_id)
        if not snapshot_path or not os.path.exists(snapshot_path):
            return None

        try:
            with open(snapshot_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None