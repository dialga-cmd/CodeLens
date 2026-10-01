"""Shared test setup.

``app`` is a package inside this directory rather than an installed one, so the
server root goes on ``sys.path`` here instead of in every test module.

The suite runs without API keys. Nothing in it clones a repository, calls
Nebius or calls Tavily: the parts that talk to the network are covered by the
end-to-end run in ``docs/TEST_REPORT.md``, not by unit tests that would need
credentials to pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> str:
    """An isolated data directory, so tests never touch a developer's cache."""
    target = tmp_path / "codelens-data"
    target.mkdir()
    monkeypatch.setenv("CODELENS_DATA_DIR", str(target))
    return str(target)


@pytest.fixture()
def repo(tmp_path) -> str:
    """A throwaway repository-shaped directory that really contains files."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "app.py").write_text("import os\n\n\ndef main():\n    return os.getcwd()\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    return str(root)


@pytest.fixture()
def outside(tmp_path) -> str:
    """A file next to the repository, which must never be reachable from it."""
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read me\n", encoding="utf-8")
    return str(secret)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch) -> None:
    """Fail loudly rather than silently testing against a developer's keys."""
    for name in (
        "NEBIUS_API_KEY",
        "TAVILY_API_KEY",
        "FIREBASE_SERVICE_ACCOUNT",
        "FIREBASE_SERVICE_ACCOUNT_JSON",
    ):
        monkeypatch.delenv(name, raising=False)
    os.environ.setdefault("CODELENS_REQUIRE_AUTH", "0")
