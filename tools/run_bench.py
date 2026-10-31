#!/usr/bin/env python3
"""Run every memory arm over generated tasks and write one row per (task, query style, arm).

For each task the memory store is built from the manifests at commit A by
Meristem's own manifest ingester, and every arm reads the same stored facts. The
questions are then asked against the tree at commit B. A fact counts as served
when the arm returns it. Gold comes from the task file, never from an arm.

    python tools/run_bench.py --tasks tasks.jsonl --repo PATH --out results/RUN \\
        [--floors floors.json] [--split dev|heldout] [--arms a,b,c]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_tasks  # noqa: E402

from meristem import atoms as atoms_mod  # noqa: E402
from meristem import discovery, embeddings, retrieval, router, store  # noqa: E402
from meristem.ingesters import manifest  # noqa: E402
from meristem.ingesters.base import IngestContext  # noqa: E402

ARMS = (
    "meristem",
    "meristem_no_liveness",
    "meristem_no_gate",
    "embed_only",
    "grep",
    "grep_path_check",
    "whole_file",
    "oracle",
)
FLOORED = ("meristem", "meristem_no_liveness", "embed_only")
DEFAULT_FLOOR = 0.70
QUERY_STYLES = ("query", "query_typed")
NOT_RUN = {
    "mem0": "needs a hosted or local LLM to extract memories; no paid calls in this protocol",
    "graphiti": "needs an LLM and a graph database; not installed",
    "hipporag": "needs an LLM for open information extraction; not installed",
    "vigiles": "instruction-file checker; not installed offline",
    "claudemd-auditor": "instruction-file checker; not installed offline",
    "staleguard": "instruction-file checker; not installed offline",
    "cclint": "instruction-file checker; not installed offline",
}


class CachedEmbedder:
    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name
        self.dim = inner.dim
        self._cache: dict[str, list[float]] = {}

    def embed(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = self.inner.embed(text)
        return self._cache[text]


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_state(path: Path) -> dict:
    def run(*args: str) -> str:
        res = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else ""

    return {"sha": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain"))}


def materialize(repo_path: str, commit: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in sorted(gen_tasks.ROOT_MANIFESTS):
        res = gen_tasks.git(repo_path, "show", f"{commit}:{name}")
        if res.returncode == 0:
            (dest / name).write_text(res.stdout, encoding="utf-8")
    return dest


def build_store(tree: Path, db: Path, embedder) -> None:
    conn = store.connect(db)
    store.init_schema(conn)
    ctx = IngestContext(conn=conn, root=tree, repo_id="bench", workspace_root=tree)
    manifest.run(ctx)
    conn.commit()
    try:
        discovery.discover_edges(conn, root=tree, rules={"references", "mirrors"})
    except Exception:
        pass
    embeddings.embed_pending(conn, embedder, limit=100000)
    conn.commit()
    conn.close()


def read_corpus(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, topic_key, source_ref FROM live_atoms ORDER BY topic_key").fetchall()
    out = []
    for r in rows:
        view = atoms_mod.get_atom(conn, r["id"])
        out.append(
            {
                "id": r["id"],
                "topic_key": r["topic_key"],
                "source_ref": r["source_ref"],
                "type": view.atom.type,
                "summary": view.summaries.get(50) or view.summaries.get(10, ""),
                "view": view,
            }
        )
    return out


def find_target(corpus: list[dict], task: dict) -> dict | None:
    want = f"dep:{task['dep']['ecosystem']}:{norm_name(task['dep']['name'])}"
    for a in corpus:
        if norm_name(a["topic_key"]) == norm_name(want) and a["source_ref"] == task["check"]["path"]:
            return a
    return None


def line_tokens(atom: dict) -> int:
    payload = router._atom_payload(atom["view"], 50)
    return router._approx_tokens(router.render_atom_line(payload))


def result(served: list[dict], target: dict | None, *, corpus_size: int, extra: dict | None = None) -> dict:
    ids = {a["id"] for a in served}
    return {
        "served_target": bool(target and target["id"] in ids),
        "served_any": bool(served),
        "n_served": len(served),
        "tokens": sum(line_tokens(a) for a in served),
        "corpus_size": corpus_size,
        **(extra or {}),
    }


def arm_meristem(db_template: Path, corpus, target, query, tree_b: Path, embedder, *, floor: float, verify_live: bool, copy_db: bool = True) -> dict:
    work = db_template.with_name(db_template.stem + f"-{time.monotonic_ns()}.sqlite") if copy_db else db_template
    if copy_db:
        shutil.copy(db_template, work)
    try:
        conn = store.connect(work)
        cls, _ = router.classify(query)
        n = {"navigational": 3, "semantic": 5, "contradictory": 7, "implementation": 5}[cls]
        stale: list[str] = []
        hits = retrieval.retrieve(
            conn,
            query=query,
            embedder=embedder,
            top_n=n * 2,
            repo_root=tree_b,
            verify_live=verify_live,
            verify_freshness_seconds=0,
            stale_out=stale,
        )
        views = {}
        for h in hits:
            view = atoms_mod.get_atom(conn, h.atom_id)
            if view is not None:
                views[h.atom_id] = view
        verdict = retrieval.gate(
            (h for h in hits if h.atom_id in views),
            min_relevance=floor,
            topic_of=lambda aid: views[aid].atom.topic_key,
            lexical_symbols=cls == "navigational",
        )
        by_id = {a["id"]: a for a in corpus}
        kept = []
        tokens = 0
        for h in verdict.kept[:n]:
            atom = by_id.get(h.atom_id)
            if atom is None:
                continue
            row_tokens = line_tokens(atom)
            if tokens + row_tokens > router.TURN_TOKEN_CAP:
                break
            tokens += row_tokens
            kept.append(atom)
        rel = None
        state = None
        for h in hits:
            if target and h.atom_id == target["id"]:
                rel, state = h.relevance, h.liveness_state
        conn.close()
        return result(
            kept,
            target,
            corpus_size=len(corpus),
            extra={
                "caught": bool(target and target["id"] in stale),
                "target_relevance": rel,
                "top_relevance": verdict.top_relevance,
                "target_state": state,
            },
        )
    finally:
        if copy_db:
            for suffix in ("", "-wal", "-shm"):
                Path(str(work) + suffix).unlink(missing_ok=True)


def arm_embed_only(db_template: Path, corpus, target, query, embedder, *, floor: float | None) -> dict:
    conn = store.connect(db_template)
    qvec = embedder.embed(query)
    hits = embeddings.top_k(conn, qvec, k=5, model=embedder.name)
    conn.close()
    by_id = {a["id"]: a for a in corpus}
    kept = [by_id[i] for i, score in hits if i in by_id and (floor is None or score >= floor)]
    top = max((s for _, s in hits), default=None)
    target_score = next((s for i, s in hits if target and i == target["id"]), None)
    return result(kept, target, corpus_size=len(corpus), extra={"top_relevance": top, "target_relevance": target_score})


def grep_hits(corpus: list[dict], name: str) -> list[dict]:
    wanted = norm_name(name)
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])")
    out = []
    for a in corpus:
        if a["type"] != "dependency":
            continue
        text = re.sub(r"[-_.]+", "-", a["summary"].lower())
        if pattern.search(text) or pattern.search(norm_name(a["topic_key"])):
            out.append(a)
    return out[:5]


def declared_at_b(task: dict, tree_b: Path) -> bool:
    check = task["check"]
    path = tree_b / check["path"]
    if not path.is_file():
        return False
    deps = gen_tasks.PARSERS[check["manifest"]](path.read_text(encoding="utf-8", errors="replace"))
    if deps is gen_tasks.INVALID:
        return False
    return deps.get(check["name"]) == check["spec"]


def arm_grep(corpus, target, task, *, tree_b: Path, mode: str) -> dict:
    hits = grep_hits(corpus, task["dep"]["name"])
    if mode == "path_check":
        hits = [a for a in hits if a["source_ref"] and (tree_b / a["source_ref"]).is_file()]
    elif mode == "oracle":
        hits = [a for a in hits if target and a["id"] == target["id"] and declared_at_b(task, tree_b)]
    return result(hits, target, corpus_size=len(corpus))


def arm_whole_file(corpus, target) -> dict:
    return result(corpus, target, corpus_size=len(corpus))


def run_arm(arm: str, ctx: dict, task: dict, query: str, floors: dict) -> dict:
    if "@" in arm:
        arm, value = arm.split("@", 1)
        floors = {**floors, arm: float(value)}
    corpus, target, db, tree_b, emb = ctx["corpus"], ctx["target"], ctx["db"], ctx["tree_b"], ctx["embedder"]
    if arm == "meristem":
        return arm_meristem(db, corpus, target, query, tree_b, emb, floor=floors.get("meristem", DEFAULT_FLOOR), verify_live=True)
    if arm == "meristem_no_liveness":
        return arm_meristem(db, corpus, target, query, tree_b, emb, floor=floors.get("meristem_no_liveness", DEFAULT_FLOOR), verify_live=False)
    if arm == "meristem_no_gate":
        return arm_meristem(db, corpus, target, query, tree_b, emb, floor=-2.0, verify_live=True)
    if arm == "embed_only":
        return arm_embed_only(db, corpus, target, query, emb, floor=floors.get("embed_only", DEFAULT_FLOOR))
    if arm == "grep":
        return arm_grep(corpus, target, task, tree_b=tree_b, mode="plain")
    if arm == "grep_path_check":
        return arm_grep(corpus, target, task, tree_b=tree_b, mode="path_check")
    if arm == "oracle":
        return arm_grep(corpus, target, task, tree_b=tree_b, mode="oracle")
    if arm == "whole_file":
        return arm_whole_file(corpus, target)
    raise SystemExit(f"unknown arm: {arm}")


def load_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(tasks: list[dict], repo_path: str, out_dir: Path, arms: list[str], floors: dict, embedder, styles=QUERY_STYLES, progress=True) -> list[dict]:
    work = Path(tempfile.mkdtemp(prefix="meristem-bench-"))
    rows: list[dict] = []
    stores: dict[str, tuple[Path, list[dict]]] = {}
    trees: dict[str, Path] = {}
    try:
        for i, task in enumerate(sorted(tasks, key=lambda t: (t["commit_a"], t["id"]))):
            a, b = task["commit_a"], task["commit_b"]
            if a not in stores:
                tree_a = materialize(repo_path, a, work / "trees" / f"a-{a[:12]}")
                db = work / "stores" / f"{a[:12]}.sqlite"
                db.parent.mkdir(parents=True, exist_ok=True)
                build_store(tree_a, db, embedder)
                conn = store.connect(db)
                stores[a] = (db, read_corpus(conn))
                conn.close()
            if b not in trees:
                trees[b] = materialize(repo_path, b, work / "trees" / f"b-{b[:12]}")
            db, corpus = stores[a]
            target = find_target(corpus, task) if task["type"] != "abstain" else None
            represented = target is not None if task["type"] != "abstain" else True
            ctx = {"corpus": corpus, "target": target, "db": db, "tree_b": trees[b], "embedder": embedder}
            for style in styles:
                for arm in arms:
                    started = time.perf_counter()
                    try:
                        res = run_arm(arm, ctx, task, task[style], floors)
                        err = None
                    except Exception as exc:
                        res, err = {}, f"{type(exc).__name__}: {exc}"
                    elapsed = (time.perf_counter() - started) * 1000
                    rows.append(
                        {
                            "task_id": task["id"],
                            "repo": task["repo"],
                            "type": task["type"],
                            "subtype": task["subtype"],
                            "gold": task["gold"],
                            "root_manifest": task["root_manifest"],
                            "represented": represented,
                            "query_style": style,
                            "arm": arm,
                            "ms": round(elapsed, 2),
                            "error": err,
                            **res,
                        }
                    )
            if progress and (i + 1) % 25 == 0:
                print(f"  {i + 1}/{len(tasks)} tasks", file=sys.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rows


def package_versions() -> dict:
    out = {}
    for pkg in ("meristem", "onnxruntime", "tokenizers", "numpy"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tasks", required=True, type=Path)
    ap.add_argument("--repo", required=True, help="local clone the tasks were generated from")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--floors", type=Path, help="JSON of per-arm relevance floors (default 0.70 each)")
    ap.add_argument("--split", choices=("dev", "heldout"), default="dev")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--limit", type=int, default=None, help="run only the first N tasks (smoke test)")
    args = ap.parse_args(argv)

    arms = [a for a in args.arms.split(",") if a]
    unknown = sorted(set(arms) - set(ARMS))
    if unknown:
        raise SystemExit(f"unknown arms: {unknown}")
    floors = json.loads(args.floors.read_text()) if args.floors else {}
    tasks = load_tasks(args.tasks)
    if args.limit:
        tasks = tasks[: args.limit]
    embedder = CachedEmbedder(embeddings.default_embedder())
    bench_root = Path(__file__).resolve().parent.parent
    started = time.time()
    rows = run(tasks, args.repo, args.out, arms, floors, embedder)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "results.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    meristem_root = Path(sys.modules["meristem"].__file__).resolve().parents[2]
    manifest_doc = {
        "split": args.split,
        "command": " ".join(sys.argv),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "seconds": round(time.time() - started, 1),
        "tasks_file": str(args.tasks.name),
        "tasks_sha256": sha256_file(args.tasks),
        "n_tasks": len(tasks),
        "arms": arms,
        "floors": {a: floors.get(a, DEFAULT_FLOOR) for a in FLOORED},
        "embedder": embedder.name,
        "query_styles": list(QUERY_STYLES),
        "liveness_freshness_seconds": 0,
        "bench": git_state(bench_root),
        "meristem_source": git_state(meristem_root),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": package_versions(),
        "not_run": NOT_RUN,
        "errors": sum(1 for r in rows if r["error"]),
    }
    (args.out / "run.json").write_text(json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} rows to {args.out} ({manifest_doc['errors']} errors)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
