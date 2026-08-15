# CI bookkeeping-gate integrity hole — audit of merged PRs

## Verified Observations

- `scripts/ci/bookkeeping_gate.sh`'s version-bump bypass branch (the second
  `elif` in the pre-fix script) decided `bookkeeping=true` from
  `pyproject.toml`'s own diff shape alone. It never checked whether any other
  file also changed in the same PR.
- `.github/workflows/ci.yml` gates the real `Lint, Test & Build` job on
  `needs.changes.outputs.bookkeeping == 'false'` and, when `bookkeeping` is
  `'true'`, the `bookkeeping-bypass` job posts a synthetic
  `checks.create({name: "Lint, Test & Build", conclusion: "success"})`
  against the PR head SHA — the same check name required-status-checks
  enforces, so branch protection is satisfied with zero tests run.
- The bookkeeping gate landed in PR #372 (`12ad11a`, merged into `main`).
  Scanning every commit on `main` since `12ad11a` that touched
  `pyproject.toml` with a version-only diff, cross-referenced against which
  of those commits also touched a path outside
  `openspec/**`/`docs/**`/`**/*.md`/`pyproject.toml`/`.codex-plugin/plugin.json`
  (the exact vulnerable shape), found **7** merged PRs, not the 2 the
  originating handoff brief (20260815-150306) had live-verified:

  | PR | Title | Merged | Other changed paths (excerpt) |
  |---|---|---|---|
  | #380 | `[full-1786676100] feature-2: 2.1, 3.3` | 2026-08-14T03:23:41Z | `src/worktrail/router/check_brief_staleness.py` |
  | #399 | `[full-1786749370] base: 2.1, 2.2` | 2026-08-15T00:05:43Z | `src/worktrail/orchestrator/integrate.py` |
  | #406 | `[full-1786757703] base: 1.1, 1.2, 1.3, 2.1, 6.1` | 2026-08-15T01:52:25Z | `src/worktrail/drain/drain.py`, `src/worktrail/orchestrator/agent_capacity.py`, `src/worktrail/shared/operator_config.py` |
  | #411 | `[full-1786769179] base: 1.4` | 2026-08-15T05:10:04Z | `src/worktrail/drain/drain.py` |
  | #422 | `fix(orchestrator): stop silently dropping a DONE task's commits from its group PR` | 2026-08-15T20:07:28Z | `src/worktrail/orchestrator/integrate.py`, `src/worktrail/orchestrator/live.py` |
  | #426 | `feat(run-scoped-plan-pinning): pin a run's RunPlan for the life of the run` | 2026-08-15T21:49:29Z | `src/worktrail/orchestrator/live.py` |
  | #438 | `fix(orchestrator): stop wholesale journal rewrites from destroying the plan pin` | 2026-08-15T22:00:06Z | `src/worktrail/orchestrator/live.py` |

  Verified against `gh pr view --json statusCheckRollup`: every one of these
  7 PRs shows `Lint, Test & Build` as both `SKIPPED` (the real job) and
  `SUCCESS` (the synthetic bookkeeping-bypass check) on the same head SHA —
  confirming each one merged without pytest, the orchestrator golden
  regression, or the package build ever running.
- The full suite passes on current `main` HEAD as of this audit
  (`PYTHONPATH=src pytest -q`: 3636 passed, 2 skipped, 230 subtests passed;
  `python3 -m worktrail.orchestrator.orchestrate check`: GOLDEN OK), so
  nothing currently on `main` is *known* broken — this audit did not find a
  live regression, only an unverified gap for the 7 PRs above.

## Unknowns / Missing Evidence

- Whether any of the 7 PRs' individual diffs, in isolation at their own merge
  point, would have failed CI had it actually run (i.e., whether any of them
  introduced a since-silently-fixed regression, or one masked by a later
  commit). The current-HEAD full-suite pass above only proves the
  accumulated state is fine, not that each PR was independently correct when
  it landed.
- Whether any commit strictly between two of these bypassed merges (i.e. one
  that DID run the full suite) would have caught a defect a bypassed PR
  introduced, before a later bypassed PR built on top of it.

## Hypotheses

- Given all 7 PRs are orchestrator/drain/router work from an actively
  developed area with a green current-HEAD suite, it is likely each PR was
  individually sound — but this is an inference, not a proven fact, and is
  exactly the gap the fix in this PR closes going forward.

## Validation Steps

- For definitive confidence, check out each of the 7 PRs' merge commit in
  isolation (`git checkout <sha> -- .` in a scratch worktree, or replay via
  `git worktree add ... <sha>`) and run `PYTHONPATH=src pytest -q &&
  PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` against
  that exact tree, oldest first, to catch a regression that a later PR might
  have silently papered over.
- This is deliberately **not** performed as part of this PR — it is a
  distinct verification task from fixing the gate mechanism itself (PR scope
  discipline: different purpose, different PR). Tracked as a handoff brief
  for follow-up.

## Confirmed Root Cause

`scripts/ci/bookkeeping_gate.sh`'s version-bump bypass branch decided
`bookkeeping=true` from `pyproject.toml`'s own diff shape alone, without
checking whether any other file in the same diff also changed — allowing a
version-bump commit that also touches real code (e.g. `src/worktrail/**`) to
silently skip the entire test suite while still satisfying the
`Lint, Test & Build` required status check via a synthetic success. Fixed in
this PR by additionally requiring that no changed path outside
`openspec/**`/`docs/**`/`**/*.md`/`pyproject.toml`/`.codex-plugin/plugin.json`
is present before the version-bump bypass can fire — see
`scripts/ci/bookkeeping_gate.sh` and the updated
`openspec/specs/ci-bookkeeping-changes-gate/spec.md`.

## Recommended Fix

Implemented in this PR (`scripts/ci/bookkeeping_gate.sh`,
`.github/workflows/ci.yml`, `scripts/ci/test_bookkeeping_gate.sh`,
`openspec/specs/ci-bookkeeping-changes-gate/spec.md`). The retroactive
per-PR verification described in Validation Steps above is out of scope for
this PR and is captured as a separate handoff brief.

## Considered and declined: neutral check instead of success

The handoff brief also asked to "consider whether the bypass job should post
a NEUTRAL check rather than 'success'." Declined for this PR: GitHub's
required-status-checks enforcement is documented against a `success`
conclusion, and switching the synthetic check's conclusion to `neutral`
risks making genuinely bookkeeping-only PRs (pure docs/openspec changes, or
a real standalone version bump) unmergeable under branch protection without
independently verifying `neutral` is treated as passing by this repo's
ruleset. That verification is straightforward to do (open a scratch PR
against a disposable branch protection rule and observe the merge button
state) but is a distinct, lower-urgency question from the integrity hole
itself, which is a correctness bug, not a UX preference — declining it here
rather than bundling an unverified change into this fix.
