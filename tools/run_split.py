#!/usr/bin/env python3
"""Run one split (dev or heldout) repo by repo with frozen floors and write a combined results folder.

    python tools/run_split.py --split dev --tasks-dir tasks/dev --corpora corpora/dev --floors tasks/dev/floors.json --out results/dev-RUN
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_bench  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=("dev", "heldout"), required=True)
    ap.add_argument("--tasks-dir", type=Path, required=True)
    ap.add_argument("--corpora", type=Path, required=True)
    ap.add_argument("--floors", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--extra-arms", default="", help="comma list such as meristem@0.7,embed_only@0.7 (sensitivity rows at a fixed floor)")
    args = ap.parse_args(argv)

    floors = json.loads(args.floors.read_text())
    embedder = run_bench.CachedEmbedder(run_bench.embeddings.default_embedder())
    arms = list(run_bench.ARMS) + [a for a in args.extra_arms.split(",") if a]
    rows: list[dict] = []
    hashes: dict[str, str] = {}
    started = time.time()
    for tasks_file in sorted(args.tasks_dir.glob("*.jsonl")):
        if tasks_file.name == "all.jsonl":
            continue
        repo = args.corpora / tasks_file.stem
        print(f"{tasks_file.stem}: running", file=sys.stderr)
        hashes[tasks_file.name] = run_bench.sha256_file(tasks_file)
        rows += run_bench.run(run_bench.load_tasks(tasks_file), str(repo), args.out, arms, floors, embedder)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "results.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    bench_root = Path(__file__).resolve().parent.parent
    meristem_root = Path(sys.modules["meristem"].__file__).resolve().parents[2]
    repo_state = {}
    for tasks_file in sorted(args.tasks_dir.glob("*.jsonl")):
        if tasks_file.name == "all.jsonl":
            continue
        state = run_bench.git_state(args.corpora / tasks_file.stem)
        count = run_bench.gen_tasks.git(str(args.corpora / tasks_file.stem), "rev-list", "--count", "HEAD").stdout.strip()
        repo_state[tasks_file.stem] = {**state, "commit_count": int(count or 0)}
    doc = {
        "split": args.split,
        "command": " ".join(sys.argv),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "seconds": round(time.time() - started, 1),
        "tasks_sha256": hashes,
        "repos": repo_state,
        "n_tasks": len({r["task_id"] for r in rows}),
        "arms": arms,
        "floors": {k: v for k, v in floors.items() if not k.startswith("_")},
        "floors_tuned_on": floors.get("_tuned_on"),
        "embedder": embedder.name,
        "query_styles": list(run_bench.QUERY_STYLES),
        "liveness_freshness_seconds": 0,
        "bench": run_bench.git_state(bench_root),
        "meristem_source": run_bench.git_state(meristem_root),
        "python": run_bench.platform.python_version(),
        "platform": run_bench.platform.platform(),
        "packages": run_bench.package_versions(),
        "not_run": run_bench.NOT_RUN,
        "errors": sum(1 for r in rows if r["error"]),
    }
    (args.out / "run.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} rows to {args.out} ({doc['errors']} errors)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
