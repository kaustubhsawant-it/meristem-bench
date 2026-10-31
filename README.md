# meristem-bench

A benchmark for one question: when a stored fact about a codebase stops being true, does a memory tool for coding agents still serve it?

Status: development split, first held-out split (Meristem commit `93292d9`, tag `paper-eval-1`) and a second held-out set for the fixed system (commit `bb1ee23`, Amendment 3) have been run. `PROTOCOL.md` is the pre-registered plan with dated amendments, and `repos.yml` lists the held-out repositories with pinned commits. All figures are self-authored.

## How to rerun

Requires Python 3.12, `uv`, and a checkout of the Meristem source (`../meristem`) with the ONNX embedder installed (`meristem embedder install`).

1. `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e "../meristem[onnx]" pytest`
2. Clone the repositories in `repos.yml` (and the development repositories listed in `PROTOCOL.md`) under `corpora/` and check out the pinned commits.
3. `python tools/gen_tasks.py corpora/dev/requests --out tasks/dev/requests.jsonl --repo-name requests --seed 20261030 --max-per-type 60 --abstain 30` (one per repository).
4. `python tools/tune_floors.py --repo-map tasks/dev/repo-map.json --out tasks/dev/floors.json` (development tasks only).
5. `python tools/run_split.py --split dev --tasks-dir tasks/dev --corpora corpora/dev --floors tasks/dev/floors.json --extra-arms meristem@0.7,embed_only@0.7,meristem@0.75 --out results/dev-RUN`
6. `python tools/score.py results/dev-RUN --json results/dev-RUN/summary.json`
7. `python tools/bench_scale.py --out results/scale-RUN.json`
8. Second held-out set: same commands with `tasks/heldout2`, `corpora/heldout2` and `--split heldout2`. To score the system before the fixes, put the `paper-eval-1` source first on `PYTHONPATH`.

`tools/dogfood_report.py` aggregates the Phase A numbers across workspaces and samples liveness catches for hand labelling. `tools/make_paper_tables.py` renders the paper tables from the summaries.

Tests: `python -m pytest tests`.

## Licence

Apache License 2.0 (see `LICENSE`). The benchmark imports Meristem, which is licensed separately under the Business Source License 1.1; the pre-registered held-out run used Meristem commit `93292d9`.
