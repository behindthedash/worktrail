## Why

`_watch_ci` (`src/worktrail/router/land_pr.py:799`) wraps `gh pr checks --watch --fail-fast`
in a loop of `WATCH_REISSUE_MAX + 1` re-issues and never looks at the PR's head SHA. When a
workflow on the target repo pushes a bot commit to the PR branch (continuum's
`release_notes_draft.yml` does this right after the first push), GitHub cancels the run set
for the old head and starts a new one for the new head. Each cancel makes the blocking watch
exit non-zero with no `fail`-bucket row, so the loop silently spends one re-issue per cancel
and returns `budget_exhausted: True` long before `watch_timeout_s` was actually used. The
pipeline then finishes the run record as `failed_recoverable` with "checks still pending at
watch budget" (`land_pr.py:1513`/`1523`) and reports `ceiling` for a PR whose CI is green a
minute later. The same bot commit leaves the remote branch one commit ahead of the local
checkout, so the next `land_pr()` invocation against the same branch (the code-defect repair
loop, or a retry after the spurious ceiling) is rejected as non-fast-forward at step 4 and
surfaces as a `push` refusal the operator has to untangle by hand. (Work-queue brief
`20260917-030300-land-pr-watch-ends-early`; the adjacent archived change
`verify-ignore-cancelled-superseded-ci-runs` fixed the same shape in `orchestrator/verify.py`
only.)

## What Changes

- The CI watch records the PR head SHA it is watching and re-reads it after every non-zero
  watch exit. A watch exit whose head SHA has moved is a **superseded** watch: it does not
  count against the re-issue budget, its `cancel`/`skipping` rows are not classified, and the
  loop re-enters against the new head. Head moves are bounded separately so a branch that
  keeps receiving pushes still terminates as `ceiling`.
- Before pushing (step 4), the pipeline fetches the remote branch. If the remote is ahead and
  every remote-only commit is bot-authored, the local branch is rebased onto the remote head
  before `git push`. Remote-only commits from a non-bot author, or a rebase that conflicts
  (aborted, tree restored), leave the push path exactly as it is today: a `push` refusal that
  quotes git's own reason.
- The run record's `checks still pending at watch budget` outcome is unchanged for a watch
  that genuinely runs out of budget on a stable head.

## Capabilities

### New Capabilities
- `land-pr-remote-head-tracking`: the landing pipeline follows the PR head across bot pushes
  during the CI watch and rebases onto bot-only remote commits before pushing.

### Modified Capabilities

## Impact

- `src/worktrail/router/land_pr.py`: `_watch_ci` gains head-SHA tracking; a new
  `_rebase_onto_bot_commits` helper runs between `_push_target` and `_push` in `land_pr()`.
- `tests/router/`: new test module for both behaviours (kept separate from
  `test_land_pr.py`, whose task chain already saturates the compile same-file gate).
- No CLI, request, or `LandOutcome` change. Callers see fewer spurious `ceiling` and `push`
  results; a genuinely exhausted watch and a genuinely diverged branch report as before.
