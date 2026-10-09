# meristem-bench

A benchmark for one question: when a stored fact about a codebase stops being true, does a memory tool for coding agents still serve it?

Status: protocol only. Nothing has been run. `PROTOCOL.md` is the pre-registered plan and `repos.yml` lists the held-out public repositories. The task generator (`tools/gen_tasks.py`) is added in the next commit.

## How it will be rerun

1. Clone the repositories in `repos.yml` at the pinned SHAs (filled in at run time).
2. The generator (`tools/gen_tasks.py`, not yet added) will build tasks from git history, deterministically and offline.
3. The scorer (not written yet) runs each arm on the same tasks and reports per-question outcomes with Wilson 95% confidence intervals.

All numbers will be labelled self-authored until someone else reruns them.

## Licence

No licence has been chosen yet, so no licence file is included. Until one is added, all rights are reserved by the author.
