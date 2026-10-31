#!/usr/bin/env python3
"""Phase A: aggregate dogfood numbers across workspaces, and sample liveness catches for hand labelling.

Two jobs, both read-only on the stores:

    python tools/dogfood_report.py summary --workspaces W1 W2 ... --days 7 --out dogfood/summary.json
    python tools/dogfood_report.py sample  --workspaces W1 W2 ... --n 20 --seed 1 --out local-results/catch-audit.csv
    python tools/dogfood_report.py audit   --csv local-results/catch-audit.csv --out dogfood/catch-audit.json

`summary` writes only counts and timings, no prompts, paths or fact text, so it can
be published after the owner has read it. `sample` writes fact keys, paths and the
git history of the checked file into a CSV that stays on this machine; the author
fills the `label` (true_catch or false_catch) and `cause` columns by comparing the
file with its history. `audit` turns the labelled CSV into precision with a Wilson
interval, plus the causes of every false catch.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import score  # noqa: E402

SUM_KEYS = (
    "turns", "inject_turns", "shadow_turns", "sessions", "inject_sessions", "shadow_sessions",
    "turns_that_injected", "empty_turns", "machine_text_skipped",
)
EVENT_KEYS = ("stale_dropped", "violation_warned", "constraint_hit", "supersession_recorded", "shadow_withheld")
CSV_FIELDS = ("workspace", "atom_id", "topic_key", "target", "pattern", "dropped_at", "last_change_commit", "last_change_date", "last_change_subject", "label", "cause")


def run_value(workspace: Path, days: int) -> dict | None:
    res = subprocess.run(["meristem", "value", "--days", str(days), "--json"], cwd=workspace, capture_output=True, text=True)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return None


def summary(workspaces: list[Path], days: int) -> dict:
    total = {k: 0 for k in SUM_KEYS}
    events = {k: 0 for k in EVENT_KEYS}
    capture = {"accepted": 0, "rejected": 0, "pending": 0}
    per_workspace = {}
    hook_p50: list[tuple[int, int]] = []
    for ws in workspaces:
        data = run_value(ws, days)
        if data is None:
            per_workspace[ws.name] = {"error": "no report"}
            continue
        for k in SUM_KEYS:
            total[k] += data.get(k, 0) or 0
        for k in EVENT_KEYS:
            events[k] += (data.get("events") or {}).get(k, 0) or 0
        for k in capture:
            capture[k] += (data.get("capture_review") or {}).get(k, 0) or 0
        hook = data.get("hook_ms") or {}
        if hook.get("n"):
            hook_p50.append((hook["p50"], hook["n"]))
        per_workspace[ws.name] = {k: data.get(k) for k in ("turns", "inject_turns", "shadow_turns", "sessions", "shadow_sessions", "turns_that_injected", "empty_turns")}
    reviewed = capture["accepted"] + capture["rejected"]
    weighted = sum(p * n for p, n in hook_p50) / sum(n for _, n in hook_p50) if hook_p50 else None
    return {
        "window_days": days,
        "workspaces": len(workspaces),
        "totals": total,
        "share_of_turns_that_injected": rate_or_none(total["turns_that_injected"], total["turns"]),
        "events": events,
        "capture": {**capture, "accept_rate": score.rate(capture["accepted"], reviewed)},
        "hook_p50_ms_weighted": weighted,
        "shadow_progress": {"sessions": f"{total['shadow_sessions']} of 10", "turns": f"{total['shadow_turns']} of 100"},
        "per_workspace": per_workspace,
    }


def rate_or_none(k: int, n: int):
    return score.rate(k, n) if n else None


def git(workspace: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def catches(workspace: Path) -> list[dict]:
    db = workspace / ".meristem" / "atoms.sqlite"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT v.atom_id, v.ts, a.topic_key, a.liveness_target, a.liveness_pattern
             FROM value_events v JOIN atoms a ON a.id = v.atom_id
            WHERE v.kind = 'stale_dropped' ORDER BY v.ts"""
    ).fetchall()
    conn.close()
    seen: dict[str, dict] = {}
    for r in rows:
        seen.setdefault(r["atom_id"], dict(r))
    out = []
    for r in seen.values():
        target = r["liveness_target"] or ""
        last = git(workspace, "log", "-1", "--format=%h%x09%cI%x09%s", "--", target) if target else ""
        commit, date, subject = (last.split("\t") + ["", "", ""])[:3]
        out.append(
            {
                "workspace": workspace.name,
                "atom_id": r["atom_id"],
                "topic_key": r["topic_key"],
                "target": target,
                "pattern": r["liveness_pattern"],
                "dropped_at": r["ts"],
                "last_change_commit": commit,
                "last_change_date": date,
                "last_change_subject": subject,
                "label": "",
                "cause": "",
            }
        )
    return out


def sample(workspaces: list[Path], n: int, seed: int, out: Path) -> int:
    pool = [row for ws in workspaces for row in catches(ws)]
    chosen = pool if len(pool) <= n else random.Random(seed).sample(pool, n)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(chosen)
    print(f"{len(pool)} catches in the logs, {len(chosen)} written to {out}", file=sys.stderr)
    return len(pool)


def audit(csv_path: Path) -> dict:
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    labelled = [r for r in rows if r["label"] in ("true_catch", "false_catch")]
    true = sum(r["label"] == "true_catch" for r in labelled)
    return {
        "sampled": len(rows),
        "labelled": len(labelled),
        "unlabelled": len(rows) - len(labelled),
        "precision": score.rate(true, len(labelled)),
        "false_catch_causes": dict(Counter(r["cause"].strip() or "unspecified" for r in labelled if r["label"] == "false_catch")),
        "complete": len(labelled) == len(rows) and len(rows) >= 20,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary")
    s.add_argument("--workspaces", nargs="+", type=Path, required=True)
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--out", type=Path)
    c = sub.add_parser("sample")
    c.add_argument("--workspaces", nargs="+", type=Path, required=True)
    c.add_argument("--n", type=int, default=20)
    c.add_argument("--seed", type=int, default=1)
    c.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("audit")
    a.add_argument("--csv", type=Path, required=True)
    a.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "summary":
        doc = summary(args.workspaces, args.days)
    elif args.cmd == "sample":
        sample(args.workspaces, args.n, args.seed, args.out)
        return 0
    else:
        doc = audit(args.csv)
    text = json.dumps(doc, indent=2, sort_keys=True)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
