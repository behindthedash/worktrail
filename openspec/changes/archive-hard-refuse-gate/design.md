## Context

See proposal.md - Why. `archive_openspec_change` (`src/worktrail/drain/drain.py`)
creates a short-lived worktree (`wt`) off the target repo's base branch and,
inside it, calls `_run_openspec_archive(wt, spec_id, timeout)`, which shells
out to `openspec archive -y <change-id>`. `worktrail.taskformats.openspec.schema`
already provides `parse_tasks_md` / `STATUS_COMPLETED`, used elsewhere in this
same file (`close_stale_bookkeeping`) to inspect a change's task checklist.

## Goals / Non-Goals

**Goals:**
- Make `_run_openspec_archive` itself refuse to shell out to
  `openspec archive` when the change's `tasks.md` (as checked out in `wt`,
  the same working tree the archive command will run in) has any
  non-completed task.
- Keep the check entirely local to `src/worktrail/drain/drain.py` — no
  changes to the finder (`find_complete_openspec_changes`), the PR-opening
  path, or `dashboard.py`'s stage detection.

**Non-Goals:**
- `src/worktrail/router/close_stale_openspec.py` is out of scope — it is a
  separate, narrower, already-adequate exception path.
- No change to how `openspec archive` itself behaves; this only adds a
  pre-check that prevents the call from happening at all when it would be
  unsafe.

## Decisions

**Check inside `_run_openspec_archive`, reading from `wt`, not `repo`.**
`_run_openspec_archive` runs after `git worktree add -b branch wt base`, so
`wt`'s `tasks.md` is exactly the file content `openspec archive` itself would
read (checked out from the repo's base branch). Reading from `repo` instead
could pass or fail against a different revision than the one actually being
archived. Placing the check inside `_run_openspec_archive` (rather than in
the caller, `archive_openspec_change`) also keeps the guard co-located with
the subprocess call it protects, and matches this file's existing pattern in
`close_stale_bookkeeping`, which already imports and calls `parse_tasks_md`
the same way.

**Locate `tasks.md` by convention (`wt / "openspec" / "changes" / spec_id / "tasks.md"`), not `resolve_spec_rel`.**
`resolve_spec_rel` is a two-format (devkit/OpenSpec) lookup used by finders
that don't yet know which format a spec is in. `_run_openspec_archive` is
only ever reached for OpenSpec changes (the finder's `format == "openspec"`
guard already restricts this), so the path is known outright; introducing
the more general helper here would add a needless devkit-path branch that
can never be taken from this call site.

**Missing `tasks.md` is not treated as a refusal.** If the file does not
exist, the pre-check is skipped and control falls through to the existing
`openspec archive` call, which will itself surface a clear failure if the
change directory or file is genuinely missing. This preserves current
behavior for that edge case rather than introducing a new failure mode
unrelated to the incomplete-tasks defect this change addresses.

## Risks / Trade-offs

[Reading `tasks.md` in the worktree instead of the canonical repo checkout
could theoretically diverge if a prior mid-flight failure left a stale
worktree with different content] → `archive_openspec_change` already resets
any leftover worktree/branch (`_reset_stale_bookkeeping_worktree`) before
`git worktree add` runs, so `wt` is always freshly checked out from `base`
immediately before this check runs.
