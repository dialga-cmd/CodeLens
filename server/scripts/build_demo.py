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

from app.core.analyzer import CodeAnalyzer  # noqa: E402
from app.core.ingestion import normalize_repo_url  # noqa: E402

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
    results = analyzer.analyze_repo(repo_url, progress_callback=lambda message: print(f"  {message}", flush=True), refresh=refresh)

    slug = target["slug"]
    filename = f"{slug}.snapshot.json"
    os.makedirs(DEMO_DIR, exist_ok=True)

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
        "analyzed_at": results.get("analyzed_at", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "files": results.get("stats", {}).get("total_files", 0),
            "hotspots": results.get("stats", {}).get("hotspot_count", 0),
            "findings": results.get("stats", {}).get("total_vulnerabilities", 0),
            "graph_edges": results.get("stats", {}).get("graph_links", 0),
        },
    }


def write_catalogue(entries: list[dict[str, Any]]) -> None:
    os.makedirs(DEMO_DIR, exist_ok=True)
    _write_json(os.path.join(DEMO_DIR, "catalogue.json"), entries)
    print(f"\nwrote catalogue.json with {len(entries)} entries", flush=True)


def main() -> int:
    refresh = "--reuse-cache" not in sys.argv
    analyzer = CodeAnalyzer()

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