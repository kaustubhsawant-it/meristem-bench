# Benchmark protocol: do memory tools serve facts that stopped being true?

Status: pre-registered draft. Nothing has been run. This file is committed before any result exists so that the pass and fail rules cannot be adjusted afterwards. Any later change is a new commit that says what changed and why.

## 0. Ground rules

- This is a new benchmark, not a reproduction of an earlier paper. Figures from earlier self-reported evaluations are not quoted here.
- Every number produced carries the words "self-authored" until someone outside the author reruns it.
- Nothing is tuned toward a target outcome. Whatever comes out is reported, including a worse result for the tool the author wrote.
- No credentials, environment files, or code that belongs to anyone else in any corpus that is published. Published tasks reference public repositories by URL and commit SHA only; no copied code.
- The run is offline: a hash embedder and no paid or hosted model calls.

## 1. What is tested

The claim worth testing: a stored fact whose check fails should not be served as true.

| Id | Hypothesis | Primary metric |
|---|---|---|
| H1 | The tool under test serves fewer falsified facts than other memory approaches | stale-serve rate: a fact served as relevant after it stopped being true |
| H2 | It does not lose live facts to achieve that | recall@k on facts that are still true |
| H3 | It injects fewer tokens for the same answer | tokens injected per query |
| H5 | The liveness catches are real | precision of catches on a hand-checked sample |

Stale-serve rate is the headline. If H1 fails, the central idea does not hold on this data, and section 5 says what follows.

## 2. Phase A: dogfood (not published)

The tool is run unchanged on the author's private development repositories for about a week. Per session the author logs queries, facts returned, facts silenced, liveness failures, and whether an injected fact was used. Bugs found are logged, not fixed, until the week ends.

End-of-week audits:
- Catch audit: at least 20 liveness catches (all, if fewer), each checked against `git diff` to see whether the code really changed. Reported as true or false catch, with the cause of each false one.
- Capture precision: every candidate fact the capture step proposed is labelled accept or reject; the accept rate is reported.
- Cost: tokens injected per turn and the share of turns that injected nothing, recomputed by a committed script.

Private repositories never enter a published corpus. Phase A results are described by method, and by an aggregate only if the author chooses to publish one, labelled not reproducible by others.

## 3. Phase B: tasks generated from git history

A task is built from two points in a repository's history. A fact is true at commit A and false at a later commit B.

Task types (target: at least 30 of each type per repository where history allows, at least 150 in total, across the five held-out repositories listed in `repos.yml`):
1. Live: the fact is still true at B. Correct behaviour: serve it.
2. Falsified: the fact was true at A and a later commit removed or changed it. Correct behaviour: do not serve it as true.
3. Abstain: nothing in memory is relevant to the query. Correct behaviour: return nothing.

Generation is scripted so that no one hand-picks easy cases. `tools/gen_tasks.py` mines each repository's history for dependency changes (`package.json`, `requirements*.txt`, `pyproject.toml`) and, in later versions, removed or renamed symbols, flipped configuration flags and renamed paths. Gold labels come from the tree at B. The first 50 gold labels are checked by hand; any error found means all are rechecked.

Corpus buckets:
- Development: private repositories of the author, used to build and tune the generator, scorer and arms. Results from them are reported as aggregates labelled not reproducible by others.
- Held-out: the public repositories in `repos.yml`, fixed before the first run and never used while developing or tuning anything. They are scored once at the end. The headline claim rests on these alone.

Held-out rule: swapping or adding a held-out repository after the first run requires a written note saying why. Commit counts in `repos.yml` are re-measured with `git rev-list --count` at clone time and recorded.

Two strata, reported separately:
- Checkable: facts a regex or AST predicate can express.
- Policy: decisions and constraints with no mechanical check. A tool that relies on checks cannot invalidate these itself, so it is expected to do no better than the baselines here. Reporting that is part of the result.

Arms (all get the same corpus, the same embedder and the same check time):
- The tool under test, with ablations: liveness off, abstention gate off, embedding-only retrieval.
- Whole-file context (an instruction file pasted in full).
- grep.
- Plain vector retrieval with the same embedder.
- Real libraries where they install and run offline: mem0 open source, Graphiti, the original HippoRAG code. If one cannot be run, its row says "not run". An approximation is allowed only if labelled "approximation of X".
- Instruction-file checkers that verify references in agent instruction files against the repository (vigiles, claudemd-auditor, Staleguard, cclint), on the checkable stratum when they install; otherwise "not run".
- Each baseline gets the same tuning budget (for example 10 configuration trials), recorded.

Scoring is a committed script with no human step. Per-question outcomes are reported, not only totals, with Wilson 95% confidence intervals for every rate. Fixed seeds, pinned Python and package versions, and the commit SHA in every output file. Three runs, one with an altered `PYTHONHASHSEED`, and a diff showing that only temporary directory names differ.

Other metrics: live recall@k, abstention precision, tokens injected, and p50 and p95 latency at 200, 1,000 and 10,000 stored facts.

## 4. Phase C: agent in the loop (not run)

No agent-in-the-loop phase is run, because it needs paid model calls. Phases A and B run entirely offline. The write-up states that nothing here measures end-to-end agent behaviour, only what the memory layer serves.

## 5. Pre-registered decision rules

Compared against the best real baseline on the held-out repositories, checkable stratum:
- Claim: the stale-serve rate is at least 15 percentage points lower, the confidence intervals do not overlap, and live recall is within 5 points. Then it may be stated that, on stale-constraint tasks in a self-authored benchmark, the tool serves fewer falsified facts, with the table.
- Reposition: a smaller or overlapping gap. Then lead with what held up (token cost, abstention, local-first setup, integration) and drop the stale-fact headline.
- Pivot: no gap, or the tool is worse. Then the result is published as a negative result and the tool is treated as a research prototype.

## 6. Rewriting earlier claims

Every number in earlier writing about the tool gets a status: verified (reproduced), changed (new value, old one struck) or removed. Documents that quote earlier figures are updated only after that.

## 7. Threats to validity

- The author wrote the system, the tasks and the baseline configurations. Mitigation: scripted generation, held-out public repositories, real libraries where possible, committed code so others can rerun.
- Git-history tasks favour facts that a predicate can check, which is the strength of the tool under test. Mitigation: the separate policy stratum.
- A few hundred tasks give wide intervals on small effects. The decision rule needs a 15-point gap for that reason.
- Well-known public repositories may appear in public training data. No model is in the retrieval path in this benchmark, so this affects only the real-library baselines, and is noted where relevant.
- Dependency changes are one fact kind among several. Early results from `gen_tasks.py` v0 cover that kind only.

## 8. Schedule

Roughly three weeks of evenings. Week 1: dogfood, generator and scorer. Week 2: generate tasks, build the arms, run development repositories, install real baselines. Week 3: held-out run, three repeat runs, write-up.
