#!/usr/bin/env python3
"""Generate live and falsified dependency tasks from a local git repository.

A task pairs two commits of one repository, A and B, and a fact that was true
at A. A "falsified" task is a fact that a later commit B removed. A "live" task
is a fact that is still true at B. Gold labels come from the manifest contents
at those commits, not from any model.

v0 covers one fact kind: a dependency is declared in package.json,
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
ABSENT = object()
INVALID = object()


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


def py_name(raw: str) -> str | None:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", raw)
    if not m:
        return None
    return re.sub(r"[-_.]+", "-", m.group(1)).lower()


def parse_package_json(text: str):
    try:
        data = json.loads(text)
    except ValueError:
        return INVALID
    if not isinstance(data, dict):
        return INVALID
    names: set[str] = set()
    for section in NPM_SECTIONS:
        block = data.get(section)
        if isinstance(block, dict):
            names.update(k for k in block if isinstance(k, str))
    return names


def parse_requirements(text: str):
    names: set[str] = set()
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            m = re.search(r"#egg=([A-Za-z0-9._-]+)", line)
            if line.startswith(("-e", "--editable")) and m:
                names.add(py_name(m.group(1)))
            continue
        line = line.split(";", 1)[0]
        name = py_name(line)
        if name:
            names.add(name)
    names.discard(None)
    return names


def parse_pyproject(text: str):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return INVALID
    names: set[str] = set()

    def add_list(items):
        for item in items or []:
            if isinstance(item, str):
                n = py_name(item)
                if n:
                    names.add(n)

    project = data.get("project", {})
    add_list(project.get("dependencies"))
    for group in (project.get("optional-dependencies") or {}).values():
        add_list(group)
    for group in (data.get("dependency-groups") or {}).values():
        add_list(group)
    poetry = (data.get("tool") or {}).get("poetry", {})
    for key in (poetry.get("dependencies") or {}):
        if key.lower() != "python":
            names.add(py_name(key))
    for group in (poetry.get("group") or {}).values():
        for key in (group.get("dependencies") or {}):
            names.add(py_name(key))
    names.discard(None)
    return names


PARSERS = {"package.json": parse_package_json, "requirements": parse_requirements, "pyproject.toml": parse_pyproject}


def check_pattern(kind: str, dep: str) -> str:
    if kind == "package.json":
        return r'"' + re.escape(dep) + r'"\s*:'
    loose = r"[-_.]+".join(re.escape(part) for part in dep.split("-"))
    if kind == "requirements":
        return r"(?im)^\s*(-e\s+.*#egg=)?" + loose + r"(?![A-Za-z0-9._-])"
    return r"(?i:[\"']\s*" + loose + r"(?![A-Za-z0-9._-]))|(?im:^\s*" + loose + r"\s*=)"


def matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


class Repo:
    def __init__(self, path: str):
        self.path = path
        self._cache: dict[tuple[str, str], object] = {}

    def text_at(self, commit: str, path: str):
        key = (commit, path)
        if key not in self._cache:
            res = git(self.path, "show", f"{commit}:{path}")
            self._cache[key] = res.stdout if res.returncode == 0 else ABSENT
        return self._cache[key]

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


def build_candidates(repo: Repo, exclude: re.Pattern, repo_name: str):
    candidates = []
    dropped = 0
    for path, commits in sorted(repo.manifest_commits(exclude).items()):
        kind = manifest_kind(path)
        parse = PARSERS[kind]
        present: dict[str, str] = {}
        last_valid = None
        for commit in commits:
            text = repo.text_at(commit, path)
            if text is ABSENT:
                present = {}
                last_valid = None
                continue
            names = parse(text)
            if names is INVALID:
                continue
            for dep in sorted(set(present) - names):
                a = present.pop(dep)
                candidates.append(("falsified", path, kind, dep, a, commit))
            for dep in sorted(names - set(present)):
                present[dep] = commit
            last_valid = commit
        if last_valid is not None:
            for dep, a in sorted(present.items()):
                if a != last_valid:
                    candidates.append(("live", path, kind, dep, a, last_valid))
    tasks = []
    for ttype, path, kind, dep, a, b in candidates:
        pattern = check_pattern(kind, dep)
        ta, tb = repo.text_at(a, path), repo.text_at(b, path)
        ok = ta is not ABSENT and tb is not ABSENT and matches(pattern, ta)
        if ok and ttype == "falsified":
            ok = not matches(pattern, tb)
        elif ok:
            ok = matches(pattern, tb)
        if not ok:
            dropped += 1
            continue
        tasks.append(
            {
                "id": task_id(repo_name, ttype, path, dep, a, b),
                "repo": repo_name,
                "commit_a": a,
                "commit_b": b,
                "fact": f"The project declares {dep} as a dependency in {path}.",
                "stratum": "checkable",
                "type": ttype,
                "gold": "serve" if ttype == "live" else "do_not_serve",
                "check": {"kind": "regex", "path": path, "pattern": pattern},
            }
        )
    return tasks, dropped


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


def generate(repo_path: str, repo_name: str | None = None, seed: int = 0, max_per_type: int | None = None, exclude: str = DEFAULT_EXCLUDE):
    repo_path = str(Path(repo_path).resolve())
    if git(repo_path, "rev-parse", "--git-dir").returncode != 0:
        raise SystemExit(f"not a git repository: {repo_path}")
    name = repo_name or os.path.basename(repo_path)
    tasks, dropped = build_candidates(Repo(repo_path), re.compile(exclude), name)
    return select(tasks, max_per_type, seed), dropped, len(tasks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", help="path to a local git repository")
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--repo-name", help="name to record in each task (default: directory name)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-per-type", type=int, default=None)
    ap.add_argument("--exclude-path-regex", default=DEFAULT_EXCLUDE)
    args = ap.parse_args(argv)
    tasks, dropped, verified = generate(args.repo, args.repo_name, args.seed, args.max_per_type, args.exclude_path_regex)
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
