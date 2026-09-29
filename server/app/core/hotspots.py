"""Deterministic hotspot ranking.

A hotspot is a file where a change is both likely to be needed and likely to be
expensive. That is measurable without a model, so the ranking is computed first
and the model is asked to explain it - not to decide it.

Every score carries the signals that produced it, so the UI can answer "why is
this a hotspot?" instead of showing an unexplained number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .git_metrics import ChurnReport

# Weights sum to 1.0. Tuned so that a single dominant signal is visible but no
# one signal can carry a file on its own.
WEIGHTS = {
    "complexity": 0.28,
    "churn": 0.24,
    "fan_in": 0.20,
    "size": 0.10,
    "findings": 0.18,
}

MIN_SCORE = 0.18


@dataclass
class Hotspot:
    file_path: str
    language: str
    score: float
    signals: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    complexity: int = 0
    fan_in: int = 0
    fan_out: int = 0
    loc: int = 0
    commits: int = 0
    finding_count: int = 0
    top_functions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "score": round(self.score, 4),
            "signals": {key: round(value, 4) for key, value in self.signals.items()},
            "reasons": self.reasons,
            "complexity": self.complexity,
            "fan_in": self.fan_in,
            "fan_out": self.fan_out,
            "loc": self.loc,
            "commits": self.commits,
            "finding_count": self.finding_count,
            "functions": self.top_functions,
            "source": "deterministic",
        }


def _normalise(values: dict[str, float]) -> dict[str, float]:
    """Scale each signal to 0..1 against the busiest file in the repository."""
    if not values:
        return {}
    peak = max(values.values())
    if peak <= 0:
        return {key: 0.0 for key in values}
    return {key: value / peak for key, value in values.items()}


def rank_hotspots(
    files: Sequence[dict[str, Any]],
    graph_metrics: dict[str, dict[str, Any]],
    churn: ChurnReport,
    findings_by_file: dict[str, list[dict[str, Any]]] | None = None,
    limit: int = 8,
) -> list[Hotspot]:
    """Rank files by how risky a change to them would be.

    ``files``      parsed file records from the analysis loop
    ``graph_metrics``  ``{file_path: {"fan_in": int, "fan_out": int}}``
    ``churn``      git history report for the same clone
    """
    findings_by_file = findings_by_file or {}

    raw: dict[str, dict[str, float]] = {
        "complexity": {},
        "churn": {},
        "fan_in": {},
        "size": {},
        "findings": {},
    }
    records: dict[str, dict[str, Any]] = {}

    for file in files:
        path = str(file.get("file_path", ""))
        if not path:
            continue
        node = graph_metrics.get(path, {})
        churn_entry = churn.files.get(path)
        findings = findings_by_file.get(path, [])

        complexity = float(file.get("complexity", 0) or 0)
        loc = float(file.get("loc", 0) or 0)
        fan_in = float(node.get("fan_in", 0) or 0)
        commits = float(churn_entry.commits) if churn_entry else 0.0
        churn_lines = float(churn_entry.total_changes) if churn_entry else 0.0
        finding_count = float(len(findings))

        records[path] = {
            "language": str(file.get("language", "unknown")),
            "complexity": int(complexity),
            "fan_in": int(fan_in),
            "fan_out": int(node.get("fan_out", 0) or 0),
            "loc": int(loc),
            "commits": int(commits),
            "churn_lines": int(churn_lines),
            "finding_count": len(findings),
            "functions": _top_functions(file.get("functions") or []),
            "last_commit": churn_entry.last_commit if churn_entry else "",
        }

        raw["complexity"][path] = complexity
        raw["churn"][path] = commits + churn_lines / 20.0
        raw["fan_in"][path] = fan_in
        raw["size"][path] = loc
        raw["findings"][path] = finding_count

    normalised = {name: _normalise(values) for name, values in raw.items()}

    hotspots: list[Hotspot] = []
    for path, record in records.items():
        signals = {name: normalised[name].get(path, 0.0) for name in WEIGHTS}
        score = sum(WEIGHTS[name] * signals[name] for name in WEIGHTS)

        if record["finding_count"] and score < MIN_SCORE:
            # A file with a deterministic security finding is always surfaced.
            score = max(score, MIN_SCORE)
        if score < MIN_SCORE:
            continue

        hotspots.append(
            Hotspot(
                file_path=path,
                language=record["language"],
                score=score,
                signals=signals,
                reasons=_reasons(record, signals, churn),
                complexity=record["complexity"],
                fan_in=record["fan_in"],
                fan_out=record["fan_out"],
                loc=record["loc"],
                commits=record["commits"],
                finding_count=record["finding_count"],
                top_functions=record["functions"],
            )
        )

    hotspots.sort(key=lambda hotspot: (-hotspot.score, hotspot.file_path))
    return hotspots[:limit]


def _top_functions(functions: Iterable[Any], count: int = 3) -> list[dict[str, Any]]:
    """The most complex functions in a file, as reported by the parser."""
    scored: list[dict[str, Any]] = []
    for function in functions:
        if isinstance(function, dict) and function.get("name"):
            scored.append(
                {
                    "name": function.get("name"),
                    "complexity": int(function.get("complexity", 0) or 0),
                    "start_line": int(function.get("start_line", 0) or 0),
                    "lines": int(function.get("lines", 0) or 0),
                }
            )
    scored.sort(key=lambda item: (-item["complexity"], -item["lines"]))
    return scored[:count]


def _reasons(record: dict[str, Any], signals: dict[str, float], churn: ChurnReport) -> list[str]:
    """Plain-language explanation of a hotspot score, strongest signal first."""
    reasons: list[str] = []

    if signals.get("complexity", 0) >= 0.6:
        detail = ""
        if record["functions"]:
            worst = record["functions"][0]
            detail = f"; worst function {worst['name']} scores {worst['complexity']}"
        reasons.append(f"Cyclomatic complexity {record['complexity']}{detail}")
    if signals.get("churn", 0) >= 0.5 and record["commits"]:
        window = f"of the last {churn.commits_scanned} commits" if churn.commits_scanned else "recent commits"
        reasons.append(f"Changed in {record['commits']} {window} - a file under active pressure")
    if signals.get("fan_in", 0) >= 0.5 and record["fan_in"] >= 2:
        reasons.append(f"Imported by {record['fan_in']} other files, so a change here has a wide blast radius")
    if record["finding_count"]:
        reasons.append(f"{record['finding_count']} security finding(s) detected by static analysis")
    if signals.get("size", 0) >= 0.7 and record["loc"] >= 400:
        reasons.append(f"{record['loc']} lines - large enough that reading it is itself a cost")
    if not reasons:
        reasons.append("Moderate complexity, connectivity and change frequency")
    return reasons
