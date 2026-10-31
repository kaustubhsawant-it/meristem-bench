#!/usr/bin/env python3
"""Score a results.jsonl from run_bench.py: rates with Wilson 95% intervals and the pre-registered verdict.

    python tools/score.py results/RUN [--population primary|all] [--query-style query|query_typed]

The primary population is the set of tasks the manifest ingester can represent
(a stored fact exists for the declaration), so every arm is scored on the same
questions. `all` adds the tasks it cannot represent, which only flatters arms
that store nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

Z = 1.959963984540054
BASELINES = ("embed_only", "grep", "grep_path_check", "whole_file")
ARM_LABELS = {
    "meristem": "Meristem",
    "meristem_no_liveness": "Meristem, liveness off",
    "meristem_no_gate": "Meristem, abstention gate off",
    "embed_only": "Embedding only (same embedder)",
    "grep": "grep on the stored facts",
    "grep_path_check": "grep + file-exists check (approximation of instruction-file checkers)",
    "whole_file": "Whole file pasted in full",
    "oracle": "Oracle (reference, not a competitor)",
}
MIN_FALSIFIED = 30


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "lo": lo if n else None, "hi": hi if n else None}


def fmt(r: dict) -> str:
    if not r["n"]:
        return "n/a"
    return f"{100 * r['rate']:.1f}% [{100 * r['lo']:.1f}, {100 * r['hi']:.1f}] ({r['k']}/{r['n']})"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    values = sorted(values)
    idx = min(len(values) - 1, max(0, math.ceil(q * len(values)) - 1))
    return values[idx]


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(rows: list[dict], population: str, style: str) -> dict:
    errors = sum(1 for r in rows if r["error"])
    rows = [r for r in rows if not r["error"] and r["query_style"] == style]
    if population == "primary":
        rows = [r for r in rows if r["represented"] and r["root_manifest"]]
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    out: dict[str, dict] = {}
    for arm, items in by_arm.items():
        fal = [r for r in items if r["type"] == "falsified"]
        live = [r for r in items if r["type"] == "live"]
        ab = [r for r in items if r["type"] == "abstain"]
        out[arm] = {
            "stale_serve": rate(sum(r["served_target"] for r in fal), len(fal)),
            "stale_serve_removed": rate(sum(r["served_target"] for r in fal if r["subtype"] == "removed"), sum(1 for r in fal if r["subtype"] == "removed")),
            "stale_serve_repinned": rate(sum(r["served_target"] for r in fal if r["subtype"] == "repinned"), sum(1 for r in fal if r["subtype"] == "repinned")),
            "live_recall": rate(sum(r["served_target"] for r in live), len(live)),
            "abstain_false_serve": rate(sum(r["served_any"] for r in ab), len(ab)),
            "mean_tokens": statistics.fmean(r["tokens"] for r in items) if items else None,
            "empty_share": rate(sum(1 for r in items if r["n_served"] == 0), len(items)),
            "p50_ms": percentile([r["ms"] for r in items], 0.5),
            "p95_ms": percentile([r["ms"] for r in items], 0.95),
            "caught": sum(1 for r in fal if r.get("caught")),
        }
    return {"errors": errors, "population": population, "query_style": style, "arms": out, "n_rows": len(rows)}


def verdict(arms: dict) -> dict:
    me = arms.get("meristem")
    base = {a: arms[a] for a in BASELINES if a in arms}
    if not me or not base:
        return {"verdict": "insufficient", "reason": "meristem or baselines missing"}
    if me["stale_serve"]["n"] < MIN_FALSIFIED:
        return {"verdict": "insufficient", "reason": f"fewer than {MIN_FALSIFIED} falsified tasks ({me['stale_serve']['n']})"}
    best_name = min(base, key=lambda a: (base[a]["stale_serve"]["rate"], -(base[a]["live_recall"]["rate"] or 0)))
    best = base[best_name]
    gap = best["stale_serve"]["rate"] - me["stale_serve"]["rate"]
    non_overlap = me["stale_serve"]["hi"] < best["stale_serve"]["lo"]
    live_ok = (me["live_recall"]["rate"] or 0) >= (best["live_recall"]["rate"] or 0) - 0.05
    if gap <= 0:
        label = "pivot"
    elif gap >= 0.15 and non_overlap and live_ok:
        label = "claim"
    else:
        label = "reposition"
    return {
        "verdict": label,
        "best_baseline": best_name,
        "gap_points": round(100 * gap, 1),
        "intervals_disjoint": non_overlap,
        "live_recall_within_5_points": live_ok,
    }


def label(arm: str) -> str:
    if arm in ARM_LABELS:
        return ARM_LABELS[arm]
    base, _, floor = arm.partition("@")
    return f"{ARM_LABELS.get(base, base)}, floor fixed at {floor}"


def render(summary: dict, run: dict | None) -> str:
    arms = summary["arms"]
    lines = []
    head = f"Population: {summary['population']}; query style: {summary['query_style']}; rows scored: {summary['n_rows']}; rows with errors dropped: {summary['errors']}"
    if run:
        head += f"\nSplit: {run.get('split')}; embedder: {run.get('embedder')}; bench {str(run['bench']['sha'])[:10]}; meristem {str(run['meristem_source']['sha'])[:10]}"
    lines.append(head + "\n")
    lines.append("| Arm | Stale-serve rate (falsified) | Live recall | Abstain false-serve | Mean tokens | Served nothing | p50 / p95 ms |")
    lines.append("|---|---|---|---|---|---|---|")
    order = [a for a in ARM_LABELS if a in arms] + sorted(a for a in arms if a not in ARM_LABELS)
    for arm in order:
        a = arms[arm]
        lines.append(
            f"| {label(arm)} | {fmt(a['stale_serve'])} | {fmt(a['live_recall'])} | {fmt(a['abstain_false_serve'])} | "
            f"{a['mean_tokens']:.0f} | {fmt(a['empty_share'])} | {a['p50_ms']:.1f} / {a['p95_ms']:.1f} |"
        )
    lines.append("")
    lines.append("Stale-serve by kind of change:")
    lines.append("")
    lines.append("| Arm | Dependency removed | Pin changed |")
    lines.append("|---|---|---|")
    for arm in order:
        a = arms[arm]
        lines.append(f"| {label(arm)} | {fmt(a['stale_serve_removed'])} | {fmt(a['stale_serve_repinned'])} |")
    v = verdict(arms)
    lines.append("")
    lines.append("Pre-registered decision rule: " + json.dumps(v, sort_keys=True))
    lines.append("All figures are self-authored.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--population", choices=("primary", "all"), default="primary")
    ap.add_argument("--query-style", choices=("query", "query_typed"), default="query")
    ap.add_argument("--json", type=Path, help="also write the summary as JSON here")
    args = ap.parse_args(argv)
    rows = load(args.run_dir)
    run_path = args.run_dir / "run.json"
    run = json.loads(run_path.read_text()) if run_path.exists() else None
    summary = summarize(rows, args.population, args.query_style)
    summary["verdict"] = verdict(summary["arms"])
    print(render(summary, run))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
