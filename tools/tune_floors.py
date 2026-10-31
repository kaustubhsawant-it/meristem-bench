#!/usr/bin/env python3
"""Pick the relevance floor for each floored arm from the development tasks only.

Meristem and embedding-only retrieval get the same grid of 10 trials. The liveness-off
ablation reuses Meristem's floor, so it differs from the full system in one thing only. The objective is the mean of
live recall, 1 - stale-serve rate and 1 - abstain false-serve rate, so no arm is
rewarded for serving everything or nothing. The chosen floors are written to a
JSON file that run_bench.py reads; held-out tasks never take part.

    python tools/tune_floors.py --tasks dev-tasks.jsonl --repo-map dev-repos.json --out floors.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_bench  # noqa: E402
import score  # noqa: E402

TUNED = ("meristem", "embed_only")
GRID = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


def objective(arm_summary: dict) -> float | None:
    parts = []
    for key, invert in (("live_recall", False), ("stale_serve", True), ("abstain_false_serve", True)):
        r = arm_summary[key]
        if not r["n"]:
            return None
        parts.append(1 - r["rate"] if invert else r["rate"])
    return sum(parts) / len(parts)


def tune(task_files: dict[str, Path], repo_paths: dict[str, str], embedder) -> dict:
    rows: list[dict] = []
    arms = [f"{arm}@{floor}" for arm in TUNED for floor in GRID]
    for repo, path in repo_paths.items():
        tasks = run_bench.load_tasks(task_files[repo])
        rows += run_bench.run(tasks, path, Path("."), arms, {}, embedder, styles=("query",), progress=False)
    summary = score.summarize(rows, "primary", "query")["arms"]
    chosen: dict[str, float] = {}
    table: dict[str, dict[str, float | None]] = {}
    for arm in TUNED:
        scores = {floor: objective(summary[f"{arm}@{floor}"]) for floor in GRID if f"{arm}@{floor}" in summary}
        table[arm] = {str(f): v for f, v in scores.items()}
        valid = {f: v for f, v in scores.items() if v is not None}
        if not valid:
            chosen[arm] = run_bench.DEFAULT_FLOOR
            continue
        best = max(valid.values())
        chosen[arm] = min((f for f, v in valid.items() if v == best), key=lambda f: abs(f - run_bench.DEFAULT_FLOOR))
    chosen["meristem_no_liveness"] = chosen["meristem"]
    return {"floors": chosen, "grid": GRID, "objective_by_floor": table, "n_rows": len(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-map", required=True, type=Path, help='JSON {"repo name": {"tasks": "path.jsonl", "repo": "path/to/clone"}}')
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    mapping = json.loads(args.repo_map.read_text())
    embedder = run_bench.CachedEmbedder(run_bench.embeddings.default_embedder())
    result = tune({k: Path(v["tasks"]) for k, v in mapping.items()}, {k: v["repo"] for k, v in mapping.items()}, embedder)
    doc = {**result["floors"], "_tuned_on": sorted(mapping), "_grid": result["grid"], "_objective_by_floor": result["objective_by_floor"], "_embedder": embedder.name}
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["floors"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
