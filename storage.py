"""Persist and load scraper runs as JSON files in ./runs/."""

import json
import os
from pathlib import Path

RUNS_DIR = Path(__file__).parent / "runs"


def save_run(run: dict) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    # Use ISO timestamp as filename, sanitized
    ts = run["timestamp"].replace(":", "-").replace("+", "Z")[:23]
    path = RUNS_DIR / f"{ts}.json"
    path.write_text(json.dumps(run, indent=2, ensure_ascii=False))
    return path


def list_runs() -> list[Path]:
    """Return run files sorted newest-first."""
    RUNS_DIR.mkdir(exist_ok=True)
    return sorted(RUNS_DIR.glob("*.json"), reverse=True)


def load_run(filename: str) -> dict:
    path = RUNS_DIR / filename
    return json.loads(path.read_text())


def load_latest_run() -> dict | None:
    runs = list_runs()
    return json.loads(runs[0].read_text()) if runs else None


def run_summaries() -> list[dict]:
    """Return lightweight metadata for all runs (no sample detail)."""
    summaries = []
    for path in list_runs():
        try:
            run = json.loads(path.read_text())
            total = sum(p["sample_count"] for p in run.get("projects", []))
            complete = sum(
                sum(1 for s in p["samples"] if s["is_complete"])
                for p in run.get("projects", [])
            )
            summaries.append({
                "filename": path.name,
                "timestamp": run.get("timestamp", ""),
                "timestamp_display": run.get("timestamp_display", path.stem),
                "project_count": run.get("project_count", 0),
                "sample_count": total,
                "complete_count": complete,
            })
        except Exception:
            pass
    return summaries
