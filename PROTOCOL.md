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

## Amendment 1 (2026-10-31, before any held-out task exists)

Written before the held-out repositories were mined or scored. Nothing here changes the decision rules in section 5.

1. Development bucket. The author's private repositories contain almost no falsified dependency events (one falsified task across four of them combined), so they cannot tune or sanity-check the arms. The development bucket now also includes five public repositories that are not on the held-out list: psf/requests, pallets/click, encode/starlette, axios/axios, fastify/fastify. Private repositories remain in Phase A only.
2. Fact kind. The checkable stratum in this round is one fact kind: a dependency is declared in a manifest at a given pin ("pypi dependency NAME pinned at SPEC (from PATH)"). The text is exactly what Meristem's manifest ingester stores. Gold is computed by `tools/gen_tasks.py` from parsed manifests at commits A and B, independently of any arm. Falsified means the dependency was removed or its pin changed; live means the declaration is unchanged at B. Removed and re-pinned are reported separately.
3. Memory under test. Every arm reads the same stored facts. The facts are produced by running Meristem's own manifest ingester over the root manifests at commit A (pyproject.toml, package.json, requirements.txt), with edge discovery and local embeddings. Tasks whose fact the ingester does not store (nested manifests, poetry or dependency-group tables, requirements files outside the root) are kept in the task file but excluded from the primary population, so no arm is scored on a question another arm could not have been asked. A second table reports all tasks.
4. Abstain tasks. Thirty per repository: a question about a popular package (from a fixed list in `gen_tasks.py`) that was never declared anywhere in the repository's manifest history, asked at a random commit. Correct behaviour is to serve nothing at all, so any served fact counts as a false serve.
5. Questions. Two fixed phrasings per task, asked against the tree at B: "Does this project depend on NAME?" (primary) and "Is `NAME` declared as a dependency in this project?" (typed identifier, reported as a second table).
6. Arms run in this round: Meristem; Meristem with liveness checks off; Meristem with the abstention gate off; embedding-only retrieval (same embedder, same floor logic); grep on the stored facts (given the package name, best case for grep); grep plus a file-exists check, labelled as an approximation of instruction-file checkers; the whole fact list pasted in full; and an oracle that applies the gold check, shown only as a reference row. Not run, and listed as such in every run file: mem0, Graphiti, HippoRAG (each needs a language model), vigiles, claudemd-auditor, Staleguard, cclint (not installable offline).
7. Tuning. The relevance floor of each floored arm (Meristem, Meristem with liveness off, embedding-only) is chosen from the same grid of 10 values (0.50 to 0.95 in steps of 0.05) on the development tasks only, maximising the mean of live recall, 1 - stale-serve rate and 1 - abstain false-serve rate. The chosen floors are frozen in `tasks/dev/floors.json` before the held-out run. The shipped default of 0.70 is reported as a sensitivity row.
8. Check time. Liveness checks are forced to run at query time (freshness 0), standing in for the time that passes between A and B. All arms use the same trees at B.
9. Held-out generation settings, fixed now: `--seed 20261030 --max-per-type 60 --abstain 30` for every held-out repository. Held-out tasks are generated and scored once, after the development run is frozen and the held-out list is approved by the owner.
10. Policy stratum. Not run in this round: it needs facts with no mechanical gold, which this generator cannot produce without hand labelling. The write-up states that the result says nothing about decisions and constraints that cannot be checked.
11. Minimum sample. The decision rule is evaluated only when the primary population has at least 30 falsified tasks; otherwise the verdict is "insufficient".
12. Known construct limits, stated in advance: the stored fact includes its pin, so a version bump falsifies it by design; re-pinned tasks are most of the falsified tasks in real histories; and the pattern Meristem checks matches the pin as text, so a pin that is a prefix of the new one can be missed. Failures of this kind are reported, not tuned away.

## Amendment 2 (2026-10-31, after the first development run, before any held-out task exists)

The first development run showed that tuning the liveness-off ablation on its own gave it a floor of 0.90, at which it served nothing on 94% of questions. That row measured a gate set to silence, not the effect of removing liveness checks. Changes:

1. Tuning applies to Meristem and to embedding-only retrieval (same grid, same objective, same tasks). The ablations (liveness off, abstention gate off) take Meristem's floor, so each differs from the full system in exactly one thing.
2. Every held-out table also carries sensitivity rows with the floor fixed at the shipped default of 0.70 for Meristem and for embedding-only retrieval, and at Meristem's tuned floor for embedding-only retrieval, so a reader can see that the ranking of arms does not depend on the tuning.
3. The tuning objective (mean of live recall, 1 - stale-serve rate, 1 - abstain false-serve rate) scores 0.667 for an arm that serves nothing. It is kept because it is fixed before the held-out run and applies equally to both tuned arms; the empty-result share is reported next to every rate so a silent arm cannot hide.
4. Development failure analysis (recorded, not acted on): all live questions Meristem missed were verified-live facts whose cosine fell below the floor; no live question was lost to a false liveness alarm. All stale facts Meristem served were dependencies declared without a version that later gained one (the check for an unpinned dependency is name-only). These are reported as limitations. No code change is made to Meristem to improve held-out numbers; the Meristem source commit used for the held-out run is recorded in `run.json`.

## Amendment 3 (2026-10-31, after the first held-out run, before any task is mined from the second set)

The first held-out run (Meristem commit `93292d9`, tag `paper-eval-1`) exposed three mechanical defects in the dependency check for Python packages: a package name spelled with different capitalisation or separators at two commits failed the fact; an environment marker after `;` was treated as part of the pin; and a dependency declared without a version stayed verified after a pin was added. They were fixed in commit `bb1ee23` before any further scoring. Changes to the protocol:

1. The first held-out result stands, unchanged, as the pre-registered result for the system as it was. Re-scoring the fixed system on the same repositories would measure a system tuned to the test, so that is not the headline for the fixed system.
2. The fixed system is tested on a second held-out set of four repositories (`repos_set2` in `repos.yml`: koa, got, fastapi, uvicorn), chosen and pinned here before any task is mined from them, not used for development or tuning, scored once. The decision rule of section 5 applies to it unchanged.
3. Floors are frozen at the development values (`tasks/dev/floors.json`) and are not retuned. Task generation uses the same settings as before: `--seed 20261030 --max-per-type 60 --abstain 30`.
4. On the second set the system before the fixes (tag `paper-eval-1`) is scored as well, so the effect of the fixes can be read on repositories neither version was developed against.
5. The fixed system is also scored on the development split and on the first held-out split. Both are labelled post-hoc in every table, because the fixes were informed by them.
6. Not fixed, and reported as limitations: weak abstention under the backticked phrasing, the recall cost of the relevance floor, and the gold labelling of a dependency declared on two lines with different environment markers (the gold rule is not changed, so the two held-out runs stay comparable).
7. Failures on the second set are reported as found; no code change is made to improve its numbers, and any change made after seeing it is a new amendment that says so.

## Amendment 4 (2026-11-14, after the second held-out run and a development pass, before any task is mined from the third set)

After the second held-out run the system was changed again (Meristem commits `9fd2c6d` and `e5f7ea9`, on top of `bb1ee23`): a stored pin that is a prefix of the new pin now counts as changed; when a question names a typed identifier, a dependency fact is served only if its name matches (the weak abstention under the backticked phrasing listed in Amendment 3, item 6); pin checks were added for cargo, go.mod and pubspec; and a package declared more than once in a manifest is stored as one fact that lists every pin. The development pass cut the backticked-phrasing abstain rate on the development split from 30.7 % to 1.3 %; the same change was also run post-hoc on both earlier held-out sets (0.0 % and 1.7 %), which informed nothing further. Changes to the protocol:

1. The two earlier held-out results stand as published. The post-hoc reruns of the changed system on them are labelled post-hoc and are not independent evidence.
2. The changed system is tested on a third held-out set of four repositories (`repos_set3` in `repos.yml`: rich, pydantic, mocha, mongoose), pinned here before any task is mined from them, not used for development or tuning, scored once. The decision rule of section 5 applies unchanged.
3. Selection rule, stated before cloning and applied by the assistant, not by the repository owner or the author: not in the development split or either earlier set, permissive licence, a root manifest in a format the task generator reads, at least 1000 commits. `execa` was cloned first, had 918 commits, failed the rule and was replaced by `mongoose` before anything was mined. No other candidate was considered.
4. Floors stay frozen at the development values (`tasks/dev/floors.json`). Task generation uses the same settings: `--seed 20261030 --max-per-type 60 --abstain 30`.
5. On the third set the system before this change (commit `bb1ee23`) is scored as well, so the effect of the change can be read on repositories neither version was developed against.
6. Failures on the third set are reported as found; no code change is made to improve its numbers, and any change made after seeing it is a new amendment that says so.
7. Self-authored as before: the same author wrote the system, the generator and the baselines.
