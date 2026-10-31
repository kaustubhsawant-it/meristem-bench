#!/usr/bin/env python3
"""Generate live, falsified and abstain dependency tasks from a local git repository.

A task pairs two commits of one repository, A and B, and a fact that was true
at A: "dependency NAME is declared in PATH pinned at SPEC". A "falsified" task
is a fact that a later commit B removed or re-pinned. A "live" task is a fact
whose declaration is unchanged at B. An "abstain" task asks about a package the
repository never declared. Gold labels come from the manifest contents at those
commits, not from any model.

v1 covers one fact kind: a dependency is declared in package.json,
requirements*.txt or pyproject.toml. No network, no model calls, stdlib only.
Python 3.11 or newer (tomllib).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

DEFAULT_EXCLUDE = r"(^|/)(node_modules|vendor|fixtures?|tests?|examples?|docs?|third_party|\.venv)(/|$)"
REQ_RE = re.compile(r"(^|/)requirements[^/]*\.txt$|(^|/)requirements/[^/]+\.txt$")
NPM_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
ROOT_MANIFESTS = frozenset({"package.json", "pyproject.toml", "requirements.txt"})
ABSENT = object()
INVALID = object()

POPULAR = {
    "pypi": [
        "numpy", "pandas", "scipy", "matplotlib", "django", "fastapi", "sqlalchemy", "celery", "redis",
        "boto3", "pillow", "scikit-learn", "tensorflow", "torch", "beautifulsoup4", "lxml", "pyyaml",
        "cryptography", "paramiko", "gunicorn", "uvicorn", "aiohttp", "tornado", "psycopg2", "pymongo",
        "selenium", "scrapy", "nltk", "spacy", "transformers", "sympy", "networkx", "plotly", "seaborn",
        "tqdm", "colorama", "arrow", "pendulum", "marshmallow", "alembic", "peewee", "docker", "fabric",
        "ansible", "sentry-sdk", "structlog", "orjson", "ujson", "msgpack", "grpcio",
    ],
    "npm": [
        "lodash", "moment", "axios", "react", "vue", "angular", "jquery", "webpack", "babel-core", "eslint",
        "prettier", "typescript", "rollup", "gulp", "grunt", "mongoose", "sequelize", "knex", "passport",
        "socket.io", "redux", "mobx", "rxjs", "d3", "three", "chalk", "commander", "yargs", "inquirer",
        "dotenv", "cors", "helmet", "morgan", "uuid", "bluebird", "async", "underscore", "ramda", "date-fns",
        "dayjs", "nodemon", "mocha", "chai", "jest", "sinon", "supertest", "cheerio", "puppeteer", "sharp",
    ],
}


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def manifest_kind(path: str) -> str | None:
    base = path.rsplit("/", 1)[-1]
    if base == "package.json":
        return "package.json"
    if base == "pyproject.toml":
        return "pyproject.toml"
    if REQ_RE.search(path):
        return "requirements"
    return None


def ecosystem(kind: str) -> str:
    return "npm" if kind == "package.json" else "pypi"


def norm_spec(raw: str) -> str:
    return re.sub(r"\s+", "", raw)


def split_requirement(raw: str) -> tuple[str, str] | None:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$", raw)
    if not m:
        return None
    name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
    return name, norm_spec(m.group(2))


def parse_package_json(text: str):
    try:
        data = json.loads(text)
    except ValueError:
        return INVALID
    if not isinstance(data, dict):
        return INVALID
    deps: dict[str, str] = {}
    for section in NPM_SECTIONS:
        block = data.get(section)
        if isinstance(block, dict):
            for k, v in block.items():
                if isinstance(k, str):
                    deps[k] = norm_spec(v) if isinstance(v, str) else json.dumps(v, sort_keys=True)
    return deps


def parse_requirements(text: str):
    deps: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            m = re.search(r"#egg=([A-Za-z0-9._-]+)", line)
            if line.startswith(("-e", "--editable")) and m:
                parsed = split_requirement(m.group(1))
                if parsed:
                    deps[parsed[0]] = "editable"
            continue
        parsed = split_requirement(line.split(";", 1)[0])
        if parsed:
            deps[parsed[0]] = parsed[1]
    return deps


def parse_pyproject(text: str):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return INVALID
    deps: dict[str, str] = {}

    def add_list(items):
        for item in items or []:
            if isinstance(item, str):
                parsed = split_requirement(item.split(";", 1)[0])
                if parsed:
                    deps[parsed[0]] = parsed[1]

    project = data.get("project", {})
    add_list(project.get("dependencies"))
    for group in (project.get("optional-dependencies") or {}).values():
        add_list(group)
    for group in (data.get("dependency-groups") or {}).values():
        add_list(group)
    poetry = (data.get("tool") or {}).get("poetry", {})

    def add_table(table):
        for key, value in (table or {}).items():
            if key.lower() == "python":
                continue
            parsed = split_requirement(key)
            if parsed:
                deps[parsed[0]] = norm_spec(value) if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)

    add_table(poetry.get("dependencies"))
    for group in (poetry.get("group") or {}).values():
        add_table(group.get("dependencies"))
    return deps


PARSERS = {"package.json": parse_package_json, "requirements": parse_requirements, "pyproject.toml": parse_pyproject}


def declared(kind: str, text: str, name: str):
    deps = PARSERS[kind](text)
    if deps is INVALID:
        return INVALID
    return deps.get(name)


class Repo:
    def __init__(self, path: str):
        self.path = path
        self._cache: dict[tuple[str, str], object] = {}
        self._parsed: dict[tuple[str, str], object] = {}

    def text_at(self, commit: str, path: str):
        key = (commit, path)
        if key not in self._cache:
            res = git(self.path, "show", f"{commit}:{path}")
            self._cache[key] = res.stdout if res.returncode == 0 else ABSENT
        return self._cache[key]

    def deps_at(self, commit: str, path: str):
        key = (commit, path)
        if key not in self._parsed:
            text = self.text_at(commit, path)
            self._parsed[key] = ABSENT if text is ABSENT else PARSERS[manifest_kind(path)](text)
        return self._parsed[key]

    def manifest_commits(self, exclude: re.Pattern) -> dict[str, list[str]]:
        res = git(
            self.path,
            "log",
            "--topo-order",
            "--reverse",
            "--no-renames",
            "--format=%x01%H",
            "--name-only",
        )
        if res.returncode != 0:
            raise SystemExit(f"git log failed: {res.stderr.strip()}")
        out: dict[str, list[str]] = defaultdict(list)
        commit = None
        for raw in res.stdout.splitlines():
            if raw.startswith("\x01"):
                commit = raw[1:]
                continue
            path = raw.strip()
            if not path or commit is None:
                continue
            if manifest_kind(path) and not exclude.search(path):
                out[path].append(commit)
        return out


def task_id(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:12]


def queries(name: str) -> dict[str, str]:
    return {
        "query": f"Does this project depend on {name}?",
        "query_typed": f"Is `{name}` declared as a dependency in this project?",
    }


def make_task(repo_name: str, ttype: str, subtype: str, path: str, kind: str, name: str, spec: str | None, a: str, b: str) -> dict:
    eco = ecosystem(kind)
    fact = None if spec is None else f"{eco} dependency {name} pinned at {spec or '*'} (from {path})."
    return {
        "id": task_id(repo_name, ttype, subtype, path, name, spec or "", a, b),
        "repo": repo_name,
        "commit_a": a,
        "commit_b": b,
        "fact": fact,
        "stratum": "checkable",
        "type": ttype,
        "subtype": subtype,
        "gold": {"live": "serve", "falsified": "do_not_serve", "abstain": "abstain"}[ttype],
        "root_manifest": path in ROOT_MANIFESTS,
        "dep": {"name": name, "ecosystem": eco, "spec_at_a": spec},
        "check": {"kind": "declared", "path": path, "manifest": kind, "name": name, "spec": spec},
        **queries(name),
    }


def build_candidates(repo: Repo, exclude: re.Pattern, repo_name: str):
    candidates = []
    dropped = 0
    ever: set[str] = set()
    commits_by_path = repo.manifest_commits(exclude)
    for path, commits in sorted(commits_by_path.items()):
        kind = manifest_kind(path)
        present: dict[str, tuple[str, str]] = {}
        last_valid = None
        for commit in commits:
            deps = repo.deps_at(commit, path)
            if deps is ABSENT:
                present = {}
                last_valid = None
                continue
            if deps is INVALID:
                continue
            ever.update(deps)
            for name in sorted(present):
                spec, a = present[name]
                if name not in deps:
                    candidates.append(("falsified", "removed", path, kind, name, spec, a, commit))
                    del present[name]
                elif deps[name] != spec:
                    candidates.append(("falsified", "repinned", path, kind, name, spec, a, commit))
                    present[name] = (deps[name], commit)
            for name in sorted(set(deps) - set(present)):
                present[name] = (deps[name], commit)
            last_valid = commit
        if last_valid is not None:
            for name, (spec, a) in sorted(present.items()):
                if a != last_valid:
                    candidates.append(("live", "unchanged", path, kind, name, spec, a, last_valid))
    tasks = []
    for ttype, subtype, path, kind, name, spec, a, b in candidates:
        da, db = repo.deps_at(a, path), repo.deps_at(b, path)
        ok = isinstance(da, dict) and isinstance(db, dict) and da.get(name) == spec
        if ok and ttype == "falsified":
            ok = db.get(name) != spec
        elif ok:
            ok = db.get(name) == spec
        if not ok:
            dropped += 1
            continue
        tasks.append(make_task(repo_name, ttype, subtype, path, kind, name, spec, a, b))
    return tasks, dropped, ever, commits_by_path


def select(tasks: list[dict], max_per_type: int | None, seed: int) -> list[dict]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(tasks, key=lambda t: t["id"]):
        by_type[t["type"]].append(t)
    chosen: list[dict] = []
    for ttype in sorted(by_type):
        items = by_type[ttype]
        if max_per_type is not None and len(items) > max_per_type:
            items = random.Random(f"{seed}:{ttype}").sample(items, max_per_type)
        chosen.extend(items)
    return sorted(chosen, key=lambda t: (t["type"], t["id"]))


def abstain_tasks(repo: Repo, commits_by_path: dict[str, list[str]], ever: set[str], repo_name: str, count: int, seed: int) -> list[dict]:
    rng = random.Random(f"{seed}:abstain")
    slots = []
    for path, commits in sorted(commits_by_path.items()):
        if path not in ROOT_MANIFESTS:
            continue
        for commit in commits:
            deps = repo.deps_at(commit, path)
            if isinstance(deps, dict) and deps:
                slots.append((path, manifest_kind(path), commit))
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    attempts = 0
    while slots and len(out) < count and attempts < count * 50:
        attempts += 1
        path, kind, commit = slots[rng.randrange(len(slots))]
        pool = [n for n in POPULAR[ecosystem(kind)] if re.sub(r"[-_.]+", "-", n).lower() not in ever and n not in ever]
        if not pool:
            continue
        name = pool[rng.randrange(len(pool))]
        if (commit, name) in seen:
            continue
        seen.add((commit, name))
        out.append(make_task(repo_name, "abstain", "abstain", path, kind, name, None, commit, commit))
    return sorted(out, key=lambda t: t["id"])


def generate(repo_path: str, repo_name: str | None = None, seed: int = 0, max_per_type: int | None = None, exclude: str = DEFAULT_EXCLUDE, abstain: int = 0):
    repo_path = str(Path(repo_path).resolve())
    if git(repo_path, "rev-parse", "--git-dir").returncode != 0:
        raise SystemExit(f"not a git repository: {repo_path}")
    name = repo_name or os.path.basename(repo_path)
    repo = Repo(repo_path)
    tasks, dropped, ever, commits_by_path = build_candidates(repo, re.compile(exclude), name)
    chosen = select(tasks, max_per_type, seed)
    if abstain:
        chosen = chosen + abstain_tasks(repo, commits_by_path, ever, name, abstain, seed)
        chosen.sort(key=lambda t: (t["type"], t["id"]))
    return chosen, dropped, len(tasks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", help="path to a local git repository")
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--repo-name", help="name to record in each task (default: directory name)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-per-type", type=int, default=None)
    ap.add_argument("--abstain", type=int, default=0, help="number of abstain tasks to add")
    ap.add_argument("--exclude-path-regex", default=DEFAULT_EXCLUDE)
    args = ap.parse_args(argv)
    tasks, dropped, verified = generate(args.repo, args.repo_name, args.seed, args.max_per_type, args.exclude_path_regex, args.abstain)
    with open(args.out, "w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    counts: dict[str, int] = defaultdict(int)
    for t in tasks:
        counts[t["type"]] += 1
    print(
        f"tasks written: {len(tasks)} {dict(sorted(counts.items()))}; "
        f"verified candidates: {verified}; dropped by consistency check: {dropped}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
