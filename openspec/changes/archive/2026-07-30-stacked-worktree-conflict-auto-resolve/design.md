## Context

`add_stacked_worktree()` (`src/worktrail/orchestrator/live.py`) creates a
task's worktree on a branch stacked off its primary dependency, then merges
every *sibling* dependency branch into it so the worktree carries all
dependency commits. Since PR #56, a sibling merge conflict aborts the merge
and raises `WorktreeStackConflictError` -- correct (it used to silently
corrupt the worktree instead), but it blocks the whole task, and every
dependent task behind it, until a human resolves the conflict by hand and
resumes the run. Incident `go-20260730-133115` hit this failure class three
times in one run.

The repo already solves the equivalent problem at PR-integration time:
`integrate.py`'s `_attempt_assembly_resolve()` spawns a bounded
`dispatch.ROLE_ASSEMBLY_RESOLVE` worker into a conflicted `git merge` state,
verifies the outcome against git state
(`_assembly_resolve_salvage()` -- no `MERGE_HEAD`, clean tree, no `<<<<<<<`
markers in the previously-conflicted files), and only trusts the resolution
once verified. `_pipeline_scheduler` already threads an injectable
`_assembly_resolve_spawn` seam down to that call. This change reuses the same
role, prompt style, and git-state verification for the stacked-worktree case,
and threads an equivalent seam into `add_stacked_worktree()`.

Three call sites create task worktrees via `add_stacked_worktree()`:
`live_run`'s `ensure_wt` (cassette/demo recording path), `live_run_real`'s
`ensure_wt`, and `full_real`'s `_ensure_wt`. Only the latter two are real run
paths.

## Goals / Non-Goals

**Goals:**
- When a sibling-dependency merge conflicts while creating a stacked task
  worktree, attempt one automatic, verified resolve before giving up.
- Preserve today's exact `WorktreeStackConflictError` raise-and-block
  behavior as the fallback whenever the resolve attempt is unavailable,
  errors, times out, or cannot be verified clean.
- Keep the new behavior fully backward compatible: `add_stacked_worktree()`
  with no resolve spawn configured behaves identically to today.

**Non-Goals:**
- Changing `live_run`'s cassette/demo `ensure_wt` -- not a production run
  path, out of scope for this change.
- Retrying more than once per conflict -- mirrors
  `integrate.ASSEMBLY_RESOLVE_STRIKES` (currently `1`), not introducing a new
  retry policy.
- Running the task's own tests/build inside the resolve step -- the
  task-level implement/test workers that run afterward in the same worktree
  already cover that; re-running them here would duplicate work the pipeline
  already does downstream.

## Decisions

**1. New optional `assembly_resolve_spawn=None` parameter on
`add_stacked_worktree()`.** Defaults to `None`, which preserves today's
immediate-raise behavior exactly -- so `live_run`'s demo path and any
existing/test caller that doesn't pass it is unaffected. Alternative
considered: make the seam mandatory and always construct a live spawn
internally. Rejected -- that would remove the ability to disable
auto-resolve, break every existing unit test that calls
`add_stacked_worktree()` directly, and contradict the "optional, injectable
seam" pattern the rest of this module already uses (`_spawn`,
`_integrate_one`, `_assembly_resolve_spawn` on `_pipeline_scheduler`).

**2. Thread the seam from `live_run_real`/`full_real` the same way
`_pipeline_scheduler` already does.** Both functions construct
`_ar_agent, _ar_model = _role_agent_model(dispatch.ROLE_ASSEMBLY_RESOLVE, ...)`
then `verify_module._make_live_spawn(_ar_model, timeout, agent=_ar_agent)`
once per run (not per task), and pass the resulting callable into their
`ensure_wt`/`_ensure_wt` closures, which forward it to
`add_stacked_worktree()`. This mirrors the existing construction at
`_pipeline_scheduler` (line ~2741) and `full_real`'s non-pipeline
`assembly_resolve_spawn=` construction (line ~3683) instead of inventing a
second pattern.

**3. New task-scoped prompt builder, not a reuse of `build_group_prompt`.**
`dispatch.build_group_prompt`'s `ROLE_ASSEMBLY_RESOLVE` branch is shaped
around a PR-integration *group* (`g["tasks"]`, group branch name, "already on
`gb`"). A stacked-worktree conflict is task-scoped: one task's fresh
worktree, one sibling dependency branch (`mb`), no group and no existing PR.
Reusing `build_group_prompt` would mean either faking a group dict or
loosening its parameter contract for a case it wasn't designed for.
Alternative considered: generalize `build_group_prompt` to accept either
shape. Rejected as unnecessary indirection for one new call site; a small
dedicated builder (e.g. `dispatch.build_stack_conflict_prompt(spec_id, task,
conflicting_branch, worktree_path)`) keeps both builders simple and mirrors
the existing action/hard-rules structure (resolve minimally preserving both
sides' intent, operate only in this worktree, no push/PR since the worktree
isn't a PR branch yet).

**4. Verify via git state before trusting the resolution, exactly like
`_assembly_resolve_salvage()`.** After the spawn call returns (or on an
unparseable report-back), check: no `MERGE_HEAD`, clean `git status
--porcelain`, and no `<<<<<<<` marker left in any file from the
`git diff --name-only --diff-filter=U` list captured before spawning. Only
treat the conflict as resolved if all three hold. This is the same
"git state is truth over a possibly-malformed report-back" principle
`_attempt_assembly_resolve` already uses, applied to a merge inside a task
worktree instead of an integration worktree.

**5. Hard fallback, unconditionally.** Spawn exception, parsed failure,
unverified git state after the attempt, or `assembly_resolve_spawn is None`
all converge on the same path: `git merge --abort` (already the existing
`_git(wt, "merge", "--abort", ...)` call) followed by raising
`WorktreeStackConflictError` with the same message format as today. No new
code path silently proceeds past an unresolved conflict.

## Risks / Trade-offs

- [A resolve worker could produce a merge that is syntactically clean but
  semantically wrong, with no test run at this stage] → Mitigation: the
  task's own implement/test workers still run inside the same worktree
  immediately afterward as part of normal task execution, and this change
  requires validation against a real multi-sibling-conflict spec (not just
  synthetic unit tests) before merging, per the incident's own lesson.
- [Added latency when a conflict actually occurs: one worker spawn +
  verification before either succeeding or falling back] → Mitigation: only
  triggered on an actual sibling merge conflict, which PR #56's own docstring
  calls rare (deps are usually a chain); bounded to one attempt
  (`ASSEMBLY_RESOLVE_STRIKES`), same bound the integration-time path already
  accepts.
- [A resolve worker could touch files outside the intended scope] →
  Mitigation: prompt explicitly scopes to "operate ONLY in this worktree"
  and lists only the conflicted files, mirroring the existing
  `ROLE_ASSEMBLY_RESOLVE` prompt's constraints.
- [False-positive "resolved" verification: markers gone but content still
  wrong] → Mitigation: same trust boundary the PR-integration path already
  operates under today; not a new risk introduced by this change, and
  downstream task-level tests are the real check.

## Migration Plan

Pure additive code change behind an optional parameter -- no data migration.
Roll out via normal PR + CI. Rollback is a plain revert: with the seam
removed, `add_stacked_worktree()` reverts to always raising on conflict,
identical to current `main`.

## Open Questions

None outstanding -- scope, fallback behavior, and validation requirements are
fixed by the proposal and incident lesson.
