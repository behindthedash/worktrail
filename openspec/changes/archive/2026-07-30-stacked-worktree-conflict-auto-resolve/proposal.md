## Why

`add_stacked_worktree()` merges every sibling dependency branch into a freshly
created task worktree. When a sibling merge conflicts, PR #56 made it raise
`WorktreeStackConflictError` and abort the run for that task rather than
silently proceeding with missing commits -- a correctness fix. But the run
then blocks unattended until a human hand-resolves the conflict and resumes,
which is exactly what happened three times in incident `go-20260730-133115`
in a single run. The repo already runs an equivalent conflict-resolution
worker (`dispatch.ROLE_ASSEMBLY_RESOLVE`) unattended at PR-integration time
(`integrate.py`'s `_attempt_assembly_resolve()`), so the same mechanism can
close this gap now, before a human has to intervene.

## What Changes

- Add an automatic resolve-and-retry path inside `add_stacked_worktree()`:
  when a sibling merge conflicts, spawn a resolve worker (role
  `ROLE_ASSEMBLY_RESOLVE`) scoped to just the conflicting file(s) in the new
  task worktree, mirroring `_attempt_assembly_resolve()`'s prompt/retry
  pattern from `integrate.py`.
- Thread a new `assembly_resolve_spawn` seam from `full_real`/`live_run_real`
  down through the stacked-worktree call path to `add_stacked_worktree()`,
  mirroring the existing `_assembly_resolve_spawn` seam already threaded
  through `_pipeline_scheduler`.
- Verify the resolve worker's outcome against actual git state (merge
  concluded, clean tree, no conflict markers in the previously-conflicted
  files) before treating the conflict as resolved -- the same
  git-state-is-truth check `_assembly_resolve_salvage()` already uses.
- Preserve today's `WorktreeStackConflictError` raise-and-block behavior as a
  hard fallback: if the resolve worker errors, reports failure, times out, or
  its outcome cannot be verified clean, abort the merge and raise exactly as
  before. Never silently proceed on an unresolved conflict.
- No **BREAKING** changes: the default behavior when no resolve spawn is
  configured (or the resolve attempt fails) is unchanged from today.

## Capabilities

### New Capabilities
- `stacked-worktree-conflict-resolution`: automatic, verified resolve-and-retry
  for sibling-dependency merge conflicts encountered while creating a stacked
  task worktree, with a hard fallback to the existing block-and-raise behavior.

### Modified Capabilities

(none -- no existing `openspec/specs/` capabilities predate this change)

## Impact

- `src/worktrail/orchestrator/live.py`: `add_stacked_worktree()` gains an
  optional `assembly_resolve_spawn` parameter and conflict-resolution logic;
  `full_real`/`live_run_real` and any other caller that constructs the spawn
  seams gain a new seam to thread through to `ensure_wt`/`add_stacked_worktree`.
  `WorktreeStackConflictError` remains defined and is still raised on
  unresolved/unverified conflicts.
- `src/worktrail/orchestrator/dispatch.py`: reuses the existing
  `ROLE_ASSEMBLY_RESOLVE` prompt-building path (`build_group_prompt`); no new
  role is introduced.
- Tests: `tests/` gains coverage for the new resolve-and-retry path (spawn
  seam invoked on conflict, verified-clean acceptance, fallback raise on
  spawn failure/timeout/unverified state) mirroring the existing
  `_attempt_assembly_resolve` test coverage, plus a real multi-sibling-conflict
  spec run (not synthetic-only) per the incident's lesson that unit tests alone
  missed this failure class.
- No API/CLI surface change for `worktrail-live`/`worktrail-full-real` callers;
  this is an internal reliability improvement to the existing stacked-worktree
  fan-out path.
