#!/usr/bin/env python3
"""Query latency at 200, 1,000 and 10,000 stored facts for Meristem and the cheap arms.

Facts are synthetic dependency declarations written through the manifest ingester's own
assert_fact call, so the store has the shape the other benchmarks use. Each size is
queried 100 times with fresh package names; times include embedding the query.

    python tools/bench_scale.py --out results/scale.json
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_bench  # noqa: E402
import score  # noqa: E402

from meristem import embeddings, store  # noqa: E402
from meristem.atoms import Liveness, assert_fact  # noqa: E402
from meristem.ingesters.base import token_pattern  # noqa: E402

SIZES = (200, 1000, 10000)
QUERIES = 100
SYLLABLES = ("ka", "lo", "mi", "ra", "ten", "vo", "su", "ne", "pi", "do", "fa", "gri", "zen", "bol", "tru")


def name_for(i: int, rng: random.Random) -> str:
    return "".join(rng.choice(SYLLABLES) for _ in range(3)) + f"-{i}"


def build(tree: Path, db: Path, count: int, embedder, rng: random.Random) -> list[str]:
    conn = store.connect(db)
    store.init_schema(conn)
    names = []
    lines = []
    for i in range(count):
        name = name_for(i, rng)
        names.append(name)
        spec = f">={rng.randint(1, 9)}.{rng.randint(0, 20)}"
        lines.append(f"{name}{spec}")
        assert_fact(
            conn,
            type="dependency",
            topic_key=f"dep:pypi:{name}",
            summary_10w=f"pypi dep {name}",
            summary_50w=f"pypi dependency {name} pinned at {spec} (from requirements.txt).",
            source_kind="manifest",
            source_ref="requirements.txt",
            liveness=Liveness(kind="regex", target="requirements.txt", pattern=token_pattern(name)),
            repo_id="bench",
            workspace_id="default",
        )
    conn.commit()
    (tree / "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    embeddings.embed_pending(conn, embedder, limit=count + 10)
    conn.commit()
    conn.close()
    return names


def measure(db: Path, tree: Path, names: list[str], embedder, arm: str, rng: random.Random) -> list[float]:
    conn = store.connect(db)
    corpus = run_bench.read_corpus(conn)
    conn.close()
    times = []
    for _ in range(QUERIES):
        name = rng.choice(names) if rng.random() < 0.5 else f"absent-{rng.randint(0, 10**6)}"
        query = f"Does this project depend on {name}?"
        started = time.perf_counter()
        if arm == "meristem":
            run_bench.arm_meristem(db, corpus, None, query, tree, embedder, floor=0.75, verify_live=True, copy_db=False)
        elif arm == "embed_only":
            run_bench.arm_embed_only(db, corpus, None, query, embedder, floor=0.75)
        elif arm == "grep":
            run_bench.grep_hits(corpus, name)
        times.append((time.perf_counter() - started) * 1000)
    return times


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sizes", default=",".join(map(str, SIZES)))
    args = ap.parse_args(argv)
    embedder = embeddings.default_embedder()
    rng = random.Random(20261031)
    report = {"embedder": embedder.name, "queries_per_size": QUERIES, "platform": platform.platform(), "python": platform.python_version(), "sizes": {}}
    for size in (int(s) for s in args.sizes.split(",")):
        work = Path(tempfile.mkdtemp(prefix="meristem-scale-"))
        try:
            db = work / "atoms.sqlite"
            names = build(work, db, size, embedder, rng)
            entry = {}
            for arm in ("meristem", "embed_only", "grep"):
                times = measure(db, work, names, embedder, arm, rng)
                entry[arm] = {"p50_ms": round(score.percentile(times, 0.5), 2), "p95_ms": round(score.percentile(times, 0.95), 2)}
            report["sizes"][str(size)] = entry
            print(size, json.dumps(entry), file=sys.stderr)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
