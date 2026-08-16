## Why

`docs/specs/research/concurrent-go-dispatch-brief-claim-race.md` documents a real
incident where a losing session's git worktree and branch were both deleted while a
shell was still `cd`'d into that worktree mid pre-PR-gate command, destroying
in-progress uncommitted work with no warning. The investigation confirmed root cause
for the *duplicate-dispatch* half of the incident (fixed by PR #479's
`same_dispatch`/`fresh` liveness primitive on `run_record.py liveness`), but left a
second question explicitly unconfirmed: **what actually removed the losing session's
worktree and branch.** Neither documented cleanup pathway (`cleanup-worktrees`,
spec-scoped `active-conflicts` staleness reconciliation) explains it under its own
stated preconditions, and the mechanism was never identified. Every `git worktree
remove`/`git branch -D` call site in this codebase today deletes on local filesystem
state alone (dirty/merged/gone), with no check for whether another live session still
owns and is actively working in that worktree — so the same data-loss symptom can
recur through this or any other undiscovered triggering path. PR #479 already shipped
the primitive needed to close this gap (`run_record.py liveness`, reporting
`fresh`/`same_dispatch` for a run record); it is wired into exactly one call site
(Active-run-resume) and not into any of the deletion paths.

## What Changes

- Add a `run_record.py` read-only lookup (`find-by-worktree`) that, given a worktree
  path and a run-records directory, returns the run record (if any) whose `worktree`
  field matches — the primitive needed to answer "which run record, if any, owns this
  worktree" before deleting it. The `worktree` field already exists on every run
  record (`cmd_start`) but is currently never populated by any call site; this change
  also wires `run_record.py set "$RUN" worktree "$WT"` into the three worktree-setup
  procedures in `subagent-prompts.md` (`#spec-worktree-setup`,
  `#change-spec-worktree-setup`, `#fix-branch-worktree-setup`) so the field is
  reliably populated going forward.
- Add a shared worktree-deletion liveness guard procedure to `subagent-prompts.md`:
  before any `git worktree remove`/`git branch -D` pair, look up the owning run record
  via `find-by-worktree`, run `worktrail-run-record liveness` on it with the caller's
  own `$INVOCATION_CONTEXT_DISPATCH_ID`, and refuse the deletion (report, don't
  destroy) when the result is `fresh: true` and `same_dispatch: false` — i.e. a
  different, still-actively-working session owns this worktree.
- Wire that guard into the three call sites named in the incident's Recommended Next
  Route: the `new`-pipeline teardown under `#worktree-lifecycle`, the direct
  fix-branch worktree teardown (`#fix-branch-worktree-teardown`), and the
  dashboard-picker `cleanup-worktrees` flow (`worktree-cleanup.md`) — the latter using
  the repo's policy-resolved `run_record_dir` (via `worktrail-policy --json`) since
  that flow has no `$RUN` of its own to derive the run-records directory from.
- This guard is defense-in-depth against the *symptom* (destructive deletion of a
  live session's work), independent of whichever undiscovered mechanism triggers a
  deletion call — it does not require confirming the incident's still-open root
  cause to be effective.

## Capabilities

### New Capabilities
- `worktree-deletion-liveness-guard`: before any documented worktree/branch removal,
  identify the run record that owns the target worktree and refuse the removal when
  that record is live and owned by a different dispatch than the caller.

### Modified Capabilities
(none — no existing capability's requirements change)

## Impact

- `src/worktrail/router/run_record.py`: new `find-by-worktree` subcommand (read-only
  scan of `<dir>/<repo-name>/*.yaml`, reusing the existing `_load_lenient` malformed-
  record handling).
- `skills/worktrail-go/references/subagent-prompts.md`: three `worktree` field writes
  at worktree-creation time, plus one new shared guard procedure invoked from the two
  teardown sections.
- `skills/worktrail-go/references/worktree-cleanup.md`: the classify/confirm/prune
  step gains the same guard before pruning each confirmed-stale worktree.
- `tests/router/test_run_record.py`: coverage for `find-by-worktree` (match, no
  match, malformed record skipped, multiple records for the same repo).
