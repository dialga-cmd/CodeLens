"""Generate the stored analyses behind the demo button.

Run this once, with real keys in ``server/.env``, against the repositories chosen
for the demo::

    python -m scripts.build_demo

It runs the ordinary pipeline - clone, parse, pre-scan, graph, hotspots, Tavily
research, model review - and writes what it produced to ``server/demo``, together
with a catalogue the API reads to list them.

Only the analysis is kept. The clone is not committed, so anything that needs the
files on disk reports itself unavailable instead of failing quietly. Re-running
this script is how the demo is refreshed: the entries are keyed by repository, so
a stale one is replaced rather than accumulated.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv  # noqa: E402

# The docstring above says to run this with real keys in server/.env. Without this
# line the keys are not read, no error is raised, and the demo snapshots are
# written with no triage, no architecture summary and no grounded sources - which
# looks like a working demo and is not one.
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

from app.core.analyzer import CodeAnalyzer  # noqa: E402
from app.core.ingestion import normalize_repo_url  # noqa: E402
from app.core.llm import get_llm_client  # noqa: E402

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))

# The repositories used to show the product working. Chosen for being small
# enough to analyse quickly and different enough to show the pipeline is not
# tuned to one language.
DEMO_TARGETS: list[dict[str, str]] = [
    {
        "slug": "flask",
        "label": "Flask - Python web framework",
        "repo_url": "https://github.com/pallets/flask",
    },
    {
        "slug": "zod",
        "label": "Zod - TypeScript schema library",
        "repo_url": "https://github.com/colinhacks/zod",
    },
]


def build(target: dict[str, str], analyzer: CodeAnalyzer, refresh: bool) -> dict[str, Any]:
    repo_url = normalize_repo_url(target["repo_url"])
    print(f"\n=== {target['label']} ({repo_url}) ===", flush=True)
    # analyze_repo already prints each progress message; a callback that printed
    # again showed every line twice.
    results = analyzer.analyze_repo(repo_url, refresh=refresh)

    slug = target["slug"]
    filename = f"{slug}.snapshot.json"
    os.makedirs(DEMO_DIR, exist_ok=True)

    stats = results.get("stats") or {}
    summary = results.get("security_summary") or {}

    # The stored snapshot is the analysis plus its identity. The clone path is
    # dropped here so it can never leak into a committed file.
    snapshot_path = os.path.join(DEMO_DIR, filename)
    snapshot = dict(results)
    snapshot["repo_path"] = ""
    _write_json(snapshot_path, snapshot)

    size = os.path.getsize(snapshot_path)
    print(f"  wrote {filename} ({size / 1024:.0f} KB)", flush=True)

    return {
        "slug": slug,
        "label": target["label"],
        "repo_url": repo_url,
        "snapshot": filename,
        "head_sha": results.get("head_sha", ""),
        # analyzed_at lives in stats, not at the top level. Reading it from the top
        # level is why every catalogue entry said the demo had never been analysed.
        "analyzed_at": stats.get("analyzed_at", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "files": stats.get("total_files", 0),
            "hotspots": stats.get("hotspot_count", 0),
            # Counted, not raw: the model's dismissals are reported next to this
            # rather than folded into it, which is what the report itself shows.
            "findings": summary.get("total", 0),
            "dismissed": summary.get("dismissed", 0),
            "grounded": stats.get("grounded_findings", 0),
            "advisories": stats.get("vulnerable_dependencies", 0),
            "graph_edges": stats.get("graph_links", 0),
        },
    }


def write_catalogue(entries: list[dict[str, Any]]) -> None:
    os.makedirs(DEMO_DIR, exist_ok=True)
    _write_json(os.path.join(DEMO_DIR, "catalogue.json"), entries)
    print(f"\nwrote catalogue.json with {len(entries)} entries", flush=True)


def main() -> int:
    refresh = "--reuse-cache" not in sys.argv
    analyzer = CodeAnalyzer()

    llm = get_llm_client()
    if not llm.configured:
        print(
            "NEBIUS_API_KEY is not set. The demo would be static analysis only - no triage, no\n"
            "architecture summary, no Fix Advisor - and it would still look complete. Put the\n"
            "key in server/.env and run this again.",
            flush=True,
        )
        return 2
    if not analyzer.research.available:
        print(
            "TAVILY_API_KEY is not set. Dependencies will be listed without advisories and the\n"
            "findings will carry no sources.",
            flush=True,
        )
    print(f"building demos with {llm.heavy_model} for triage and {llm.fast_model} for chat", flush=True)

    entries: list[dict[str, Any]] = []
    failures: list[str] = []
    for target in DEMO_TARGETS:
        try:
            entries.append(build(target, analyzer, refresh))
        except Exception as error:  # noqa: BLE001 - one bad target must not lose the rest
            print(f"  FAILED: {type(error).__name__}: {error}", flush=True)
            failures.append(target["slug"])

    if entries:
        write_catalogue(entries)
    print(f"\nbuilt {len(entries)}/{len(DEMO_TARGETS)} demo analyses", flush=True)
    if failures:
        print(f"failed: {', '.join(failures)}", flush=True)
        return 1
    return 0


def _write_json(path: str, payload: Any) -> None:
    """Write through a temporary file, so a partial file is never left behind."""
    import tempfile

    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=1)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())