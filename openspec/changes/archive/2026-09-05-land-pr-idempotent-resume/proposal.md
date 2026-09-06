## Why

Re-invoking `land_pr()` against a branch that is already pushed — and whose
PR is already open or already merged — re-pays the entire pre-PR gate
(work-queue brief `20260905-212233-worktrail-land-pr-re-runs`). Confirmed by
reading `land_pr()` at `src/worktrail/router/land_pr.py:1019-1090`: the
pipeline unconditionally runs `_commit_pending` -> `_ensure_compile_markers`
(which fetches the base branch and recompiles every touched OpenSpec change)
-> `_run_preflight_and_labels` (the full pre-PR gate) -> `_push`, and only
then reaches `open_or_update_pull_request`. There is no read of an existing
run record's `pull_request` value anywhere before the gate — the single
`pull_request` reference in the module is a `run_record set` *write* at line
1179, after it — and no comparison of the remote branch tip against `HEAD`.
So a resumed or retried landing (an interrupted CI watch, a fresh-context
re-run, a drain iteration re-reaching the same branch) redoes minutes of gate
work that provably cannot change anything: the commit it would gate is the
commit already on the remote.

The same gap has a second, sharper edge. `open_or_update_pull_request` only
short-circuits when `gh pr view <branch>` reports state `OPEN`; a `MERGED` or
`CLOSED` PR falls through to the `gh pr create` literal at line ~623. So
re-invoking against an already-merged branch not only re-pays the gate, it
then attempts to open a *second* PR for commits that are already on the base
branch.

## What Changes

- `land_pr()` SHALL, before step 1 (`_commit_pending`), evaluate a resume
  fast path: when the working tree is clean, the current branch resolves,
  the push remote's tip for that branch equals local `HEAD`, and a PR
  already exists for the branch, the commit / compile-marker / preflight /
  push steps (1-4) SHALL be skipped. There is nothing to commit, nothing new
  to gate, and nothing to push — the gated artifact is already on the remote.
- An already-`MERGED` PR found by the fast path SHALL be reported as a
  terminal no-op (`landed`, `completed_and_merged`) after the run record is
  recorded and finished, and SHALL NOT reach `gh pr create`. An already-
  `CLOSED`-unmerged PR SHALL be reported as `ceiling` for human
  reconciliation, also without reaching `gh pr create`.
- An `OPEN` PR found by the fast path SHALL resume at step 5: the PR body and
  labels are still refreshed and CI is still watched to a terminal outcome,
  so an interrupted watch can be resumed without re-gating.
- Every precondition read is a fail-safe: if any git/`gh` probe fails or any
  precondition does not hold, the fast path is declined and the existing
  unconditional pipeline runs exactly as it does today.
- Regression coverage for each arm: skip-with-open-PR, merged no-op,
  closed-unmerged ceiling, dirty tree declines, diverged remote tip declines,
  no-PR declines, probe failure declines.

## Capabilities

### New Capabilities
- `land-pr-resume-fast-path`: precondition-guarded skipping of `land_pr()`
  steps 1-4 on a re-invocation whose commit is already pushed and already has
  a PR, plus explicit terminal handling of a merged or closed PR.

### Modified Capabilities
(none — `pr-landing-pipeline`'s existing requirements are untouched. The
compile-marker and preflight requirements are both scoped to "before anything
is pushed"; the fast path pushes nothing. "Refusal leaves the remote
untouched" is likewise unaffected: the fast path never returns `refused`.)

## Impact

- `src/worktrail/router/land_pr.py` — a new precondition probe plus a resume
  branch at the top of `land_pr()`. Reuses the existing `_current_branch()`,
  `_push_target()`, `_git()`, `_gh()`, `render_pr_body()`,
  `open_or_update_pull_request()`, `_ensure_run_record()`,
  `_finish_or_checkpoint()` and `pre_pr_gate.resolve_pr_labels()`; adds no
  second implementation of any of them.
- `tests/router/test_land_pr_resume.py` (new), alongside the existing
  `tests/router/test_land_pr.py` / `test_land_pr_integration.py` /
  `test_land_pr_push_refusal.py` fake-runner conventions.
- No change to any caller (`queue_triage.py`, `drain.py`,
  `orchestrator/integrate.py`, the skill prose): the `LandRequest` /
  `LandOutcome` contract is unchanged, so every caller gets the fast path
  without a call-site edit.
