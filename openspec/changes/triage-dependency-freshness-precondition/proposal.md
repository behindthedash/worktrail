## Why

Queue triage runs reproduction commands against the target repo's canonical checkout: the
mechanical premise check (`premise_check.run_premise_check`, `src/worktrail/workqueue/premise_check.py`)
executes an allow-listed test runner such as `npm test` in that checkout, and the evaluator
prompt (`EVALUATOR_PROMPT_TEMPLATE`, `src/worktrail/workqueue/queue_triage.py`) tells the
evaluator to spend its own tool calls reproducing each brief's premise there. Neither path
checks that the checkout's installed `node_modules` still matches its lockfile before treating
a command's output as evidence.

That gap produced a wrong triage verdict on 2026-09-10. The sibling brief
`20260910-102858-fix-pre-existing-order-dependent` was evaluated against a canonical checkout
whose `node_modules` held vitest 4.1.11 while `package-lock.json` pinned 5.0.0. The evaluator
ran `npx vitest run src/app/platform/layout.test.tsx`, saw `Linked modules must use the same
context`, and recorded a deterministic suite-load regression with "No candidate change fits".
The later PR #2789 note states the test passes with a correct install and no repo change
applied. Nothing in the triage note recorded that the install was stale, so the wrong verdict
was only discovered by the engineer who picked the brief up afterwards.

The only installed-tree-vs-lockfile comparison in the package is
`lockfile_matches` in `src/worktrail/orchestrator/bootstrap_node_modules.py` (PR #404), which
compares two worktrees' lockfiles to decide whether to hardlink-clone `node_modules` during
orchestrator task-worktree bootstrap. It never looks at what is actually installed and is not
on the triage path. No active change under `openspec/changes/` covers triage preconditions.

## What Changes

- A new deterministic dependency-freshness check, run once per repo group before the
  evaluator is spawned, compares each tracked npm lockfile's pinned versions for the root
  package's direct dependencies against the versions installed under the adjacent
  `node_modules`. It reports each package root as `fresh`, `stale` (a direct dependency is
  missing or installed at a different version, listed by name with locked vs installed
  versions), or `unknown` (unreadable lockfile or install tree). A repo with no tracked
  npm lockfile has no package roots and is trivially fresh. The check reads only; it never
  installs, writes, or modifies the checkout.
- The mechanical premise check no longer runs `npm test` when the package root it would run
  in is not fresh: the command needle is recorded `confirmed: false` with a detail naming
  the stale package root and the mismatched packages, so a stale tree can never produce a
  "confirmed reproduction" that `work-directly` acceptance then trusts.
- The evaluator prompt gains a per-group "Dependency freshness" block rendered from the
  check, and an instruction that reproduction output obtained through a stale install is
  not evidence for `stale-close`, `needs-update`, `work-directly`, or a "no candidate
  fits" judgement: the evaluator must fall back to `keep` and cite the stale package root
  in its evidence so the triage note records why.
- `evaluate_group()`'s result and each parsed `Verdict` carry the freshness results as
  `dependency_freshness`, alongside the existing `premise_check`, so downstream consumers
  and tests can see the precondition that applied to the run.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `intake-triage`: adds a dependency-freshness precondition ahead of evaluation and amends
  the mechanical premise check so an allow-listed npm command is not run against a stale
  install.

## Impact

- `src/worktrail/workqueue/dependency_freshness.py` (new): lockfile discovery via
  `git ls-files`, direct-dependency version comparison, and a prompt-block renderer.
- `src/worktrail/workqueue/premise_check.py`: `run_premise_check` accepts the freshness
  results and skips `npm test` for a non-fresh package root.
- `src/worktrail/workqueue/queue_triage.py`: `evaluate_group` runs the check for
  repo-bearing groups, threads it into the premise check and the prompt, and returns it;
  `parse_verdicts` copies it onto `Verdict.dependency_freshness`.
- `tests/workqueue/test_dependency_freshness.py` (new), `tests/workqueue/test_premise_check.py`,
  `tests/workqueue/test_queue_triage.py`.
- No change to `bootstrap_node_modules.py`, the orchestrator, any console script, or the
  work-queue file format. Python and other ecosystems' installs are out of scope (see
  design.md).
