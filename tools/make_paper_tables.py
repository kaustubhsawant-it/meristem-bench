#!/usr/bin/env python3
"""Render LaTeX tables for the paper from score.py summaries and the seeded liveness evaluation.

    python tools/make_paper_tables.py --summary dev=results/dev-RUN/summary.json [--summary heldout=...] \\
        --liveness ../meristem/paper/data/liveness_eval.json --out ../meristem/paper/src/tables
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import score  # noqa: E402

SHORT = {
    "meristem": "Meristem",
    "meristem_no_liveness": "\\quad liveness checks off",
    "meristem_no_gate": "\\quad abstention gate off",
    "embed_only": "Embedding only",
    "grep": "grep on stored facts",
    "grep_path_check": "grep + file-exists check$^{\\dagger}$",
    "whole_file": "Whole file in context",
    "oracle": "Oracle (reference)",
}
MAIN_ORDER = ("meristem", "meristem_no_liveness", "meristem_no_gate", "embed_only", "grep", "grep_path_check", "whole_file", "oracle")


def pct(r: dict) -> str:
    if not r["n"]:
        return "n/a"
    return f"{100 * r['rate']:.1f} [{100 * r['lo']:.1f}, {100 * r['hi']:.1f}]"


def name(arm: str) -> str:
    if arm in SHORT:
        return SHORT[arm]
    base, _, floor = arm.partition("@")
    return f"{SHORT.get(base, base)}, floor {floor}"


def main_table(summary: dict) -> str:
    arms = summary["arms"]
    order = [a for a in MAIN_ORDER if a in arms]
    lines = [
        "\\begin{tabular}{@{}lccccc@{}}",
        "\\toprule",
        "Arm & Stale-serve \\% & Live recall \\% & Abstain false-serve \\% & Tokens & Empty \\% \\\\",
        "\\midrule",
    ]
    for arm in order:
        a = arms[arm]
        empty = f"{100 * a['empty_share']['rate']:.0f}"
        lines.append(f"{name(arm)} & {pct(a['stale_serve'])} & {pct(a['live_recall'])} & {pct(a['abstain_false_serve'])} & {a['mean_tokens']:.0f} & {empty} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def sensitivity_table(summary: dict) -> str:
    arms = summary["arms"]
    order = sorted(a for a in arms if "@" in a)
    if not order:
        return ""
    lines = [
        "\\begin{tabular}{@{}lccccc@{}}",
        "\\toprule",
        "Arm & Stale-serve \\% & Live recall \\% & Abstain false-serve \\% & Tokens & Empty \\% \\\\",
        "\\midrule",
    ]
    for arm in order:
        a = arms[arm]
        empty = f"{100 * a['empty_share']['rate']:.0f}"
        lines.append(f"{name(arm)} & {pct(a['stale_serve'])} & {pct(a['live_recall'])} & {pct(a['abstain_false_serve'])} & {a['mean_tokens']:.0f} & {empty} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def kind_table(summary: dict) -> str:
    arms = summary["arms"]
    lines = ["\\begin{tabular}{@{}lcc@{}}", "\\toprule", "Arm & Dependency removed \\% & Pin changed \\% \\\\", "\\midrule"]
    for arm in (a for a in MAIN_ORDER if a in arms):
        a = arms[arm]
        lines.append(f"{name(arm)} & {pct(a['stale_serve_removed'])} & {pct(a['stale_serve_repinned'])} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def latency_table(summary: dict) -> str:
    arms = summary["arms"]
    lines = ["\\begin{tabular}{@{}lcc@{}}", "\\toprule", "Arm & p50 ms & p95 ms \\\\", "\\midrule"]
    for arm in (a for a in MAIN_ORDER if a in arms and a != "oracle"):
        a = arms[arm]
        lines.append(f"{name(arm)} & {a['p50_ms']:.1f} & {a['p95_ms']:.1f} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def liveness_table(path: Path) -> str:
    doc = json.loads(path.read_text())
    labels = {
        "schema_fact:claim": ("Table and columns", "structured claim"),
        "schema_fact:anchor-only": ("Table and columns", "anchor regex only"),
        "constraint:claim": ("Column constraint", "structured claim"),
        "constraint:declaration-regex": ("Column constraint", "declaration regex"),
        "dependency:pinned-spec": ("Dependency", "name and pin"),
        "dependency:name-only": ("Dependency", "name only"),
    }
    lines = [
        "\\begin{tabular}{@{}llccrrr@{}}",
        "\\toprule",
        "Fact & Check & Recall & Precision & TP & FN & FP \\\\",
        "\\midrule",
    ]
    for key, (fact, check) in labels.items():
        c = doc["classes"][key]
        rec = "n/a" if c["recall"] is None else f"{c['recall']:.2f}"
        prec = "n/a" if c["precision"] is None else f"{c['precision']:.2f}"
        lines.append(f"{fact} & {check} & {rec} & {prec} & {c['tp']} & {c['fn']} & {c['fp']} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def scale_table(path: Path) -> str:
    doc = json.loads(path.read_text())
    lines = ["\\begin{tabular}{@{}rcccccc@{}}", "\\toprule", "Stored facts & \\multicolumn{2}{c}{Meristem} & \\multicolumn{2}{c}{Embedding only} & \\multicolumn{2}{c}{grep} \\\\", " & p50 & p95 & p50 & p95 & p50 & p95 \\\\", "\\midrule"]
    for size, entry in sorted(doc["sizes"].items(), key=lambda kv: int(kv[0])):
        cells = " & ".join(f"{entry[a][k]:.1f}" for a in ("meristem", "embed_only", "grep") for k in ("p50_ms", "p95_ms"))
        lines.append(f"{int(size):,} & {cells} \\\\".replace(",", "{,}"))
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--liveness", type=Path)
    ap.add_argument("--scale", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for item in args.summary:
        label, _, path = item.partition("=")
        summary = json.loads(Path(path).read_text())
        (args.out / f"{label}_main.tex").write_text(main_table(summary), encoding="utf-8")
        (args.out / f"{label}_kind.tex").write_text(kind_table(summary), encoding="utf-8")
        (args.out / f"{label}_latency.tex").write_text(latency_table(summary), encoding="utf-8")
        sens = sensitivity_table(summary)
        if sens:
            (args.out / f"{label}_sensitivity.tex").write_text(sens, encoding="utf-8")
    if args.scale:
        (args.out / "scale.tex").write_text(scale_table(args.scale), encoding="utf-8")
    if args.liveness:
        (args.out / "liveness.tex").write_text(liveness_table(args.liveness), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
