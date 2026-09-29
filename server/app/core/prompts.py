"""Access to the prompt templates shipped with the backend.

Templates live in ``server/prompts`` and are plain text files so they can be
reviewed, diffed and edited without touching Python. They are provider-neutral:
the same file is used whatever model answers the request.
"""

from __future__ import annotations

import os
from functools import lru_cache

PROMPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "prompts"))


@lru_cache(maxsize=None)
def load_prompt(filename: str) -> str:
    """Read a prompt template by filename (e.g. ``analysis_prompt.txt``)."""
    path = os.path.join(PROMPTS_DIR, filename)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()
