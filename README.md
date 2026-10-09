# meristem-bench

A benchmark for one question: when a stored fact about a codebase stops being true, does a memory tool for coding agents still serve it?

Status: protocol only. Nothing has been run. `PROTOCOL.md` is the pre-registered plan and `repos.yml` lists the held-out public repositories. `tools/gen_tasks.py` is a first version of the task generator. It covers one fact kind (declared dependencies) and has been tested only on synthetic repositories built in `tests/`.

## How it will be rerun

1. Clone the repositories in `repos.yml` at the pinned SHAs (filled in at run time).
2. `python tools/gen_tasks.py <repo-path> --out tasks.jsonl [--repo-name NAME] [--seed N] [--max-per-type N]` builds live and falsified tasks from git history, deterministically and offline (stdlib only, Python 3.11+).
3. The scorer (not written yet) runs each arm on the same tasks and reports per-question outcomes with Wilson 95% confidence intervals.

Tests: `python -m unittest discover -s tests` (or `pytest tests`).

All numbers will be labelled self-authored until someone else reruns them.

## Licence

No licence has been chosen yet, so no licence file is included. Until one is added, all rights are reserved by the author.
