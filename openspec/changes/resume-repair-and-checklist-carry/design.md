# Design

## Context

See proposal.md -- Why. Relevant machinery, all in
`src/worktrail/orchestrator/live.py` unless noted:

- `ensure_wt` (live_run_real, ~live.py:3060) and `_ensure_wt` (`_pipeline_scheduler`,
  ~live.py:4199) are near-identical per-task worktree accessors: `if not wt.exists()`
  creates via `add_stacked_worktree` (which itself calls
  `_carry_squash_merged_dependencies` when `remote`/`base` are supplied); the `else`
  branch (retained worktree) only calls `_validate_retained_task_branch` and
  `_require_dependency_files` -- no carry, no repair.
- `_carry_squash_merged_dependencies` (live.py:1656) merges the freshest base ref into a
  stacked worktree for DONE-but-branch-gone dependencies. On a merge conflict it aborts
  and WARNs; `_require_dependency_files` is the fail-loud backstop immediately after.
  This behavior is spec-mandated (`openspec/specs/stacked-worktree-conflict-resolution`)
  after an earlier `-X ours` auto-resolve was found unsafe (research doc cited in
  proposal.md).
- `_apply_step_commit` (live.py:2796) and `live_run()`'s `drive()` closure
  (live.py:2308) both call `dispatch.apply_report()` to get the coordinator's new task
  status, then build the journal entry's `report` fields. Both already special-case
  `new == "escalated"` to stamp `terminal_status="escalated"` (PR #496/#498) but do
  nothing for `new == "failed"`.
- `clear_tasks()`'s `_terminal_failure()` (live.py:1169) is the sole gate
  `worktrail-live clear-task` uses to decide a journal entry is a legitimate target:
  `report.terminal_status in ("failed", "escalated")`.

## Goals / Non-Goals

**Goals:**
- A retained worktree that drifts behind a squash-merged dependency between creation and
  resume repairs itself via the same carry mechanism creation already uses, without
  discarding any in-progress work.
- The one demonstrated-safe class of squash-merge-carry conflict (the change's own
  tasks.md checklist, add/add, common ancestor lost) resolves automatically; every other
  conflict shape keeps today's fail-loud behavior unchanged, so the spec's core safety
  guarantee (no silent, non-deterministic conflict resolution) is not weakened.
- Any role's normal terminal failure (not just the review circuit-breaker's escalation)
  is recognizable by `clear_tasks()` without a hand-patched journal.

**Non-Goals:**
- No general auto-resolve for squash-merge-carry conflicts on arbitrary files --
  explicitly out of scope; the research doc that motivated the current fail-loud
  requirement stays the controlling rationale for every file except tasks.md.
- No change to `add_stacked_worktree`'s own sibling-merge conflict handling
  (`assembly_resolve_spawn`) -- unrelated code path, already spec-covered, untouched.
- No change to `_terminal_failure()`'s "never discard completed work" guardrail
  (live.py:1177-1189) -- only the failure-recognition gate changes; the completion
  guardrail already reads `terminal_status` correctly and needs no edit.

## Decisions

1. **Repair the retained-worktree branch by re-attempting the existing carry, not by
   recreating the worktree.** `_carry_squash_merged_dependencies` is already safe to call
   more than once (its `merge-base --is-ancestor` check makes a second call a no-op when
   nothing changed) and operates purely via `git merge` against the existing worktree --
   no need to `git worktree remove` + `add` again, which would also discard any
   in-progress uncommitted work in that worktree. Gate the retry narrowly on
   `WorktreeMissingDependencyFileError` specifically (not e.g. `WorktreeAddError` from the
   branch-mismatch check above it), so only the exact failure mode this change targets
   gets the new retry path.
2. **Special-case tasks.md instead of wiring `assembly_resolve_spawn` into the carry.**
   The brief's own alternative framing. Wiring `assembly_resolve_spawn` in (mirroring
   `add_stacked_worktree`'s sibling-merge step) would let an LLM resolve arbitrary
   content conflicts here, which is exactly the "auto-resolve conflicts in favor of
   either side" the spec's fail-loud requirement was hardened to forbid -- reintroducing
   the risk class research already found and removed (`-X ours`). Detecting the
   conflicted-file set via `git diff --name-only --diff-filter=U` and handling the
   single-file `openspec/changes/<change_id>/tasks.md` case with a deterministic
   checked-line union is narrow, auditable, and provably safe (a checklist union can
   never lose information -- it only ever adds `[x]` marks that at least one side already
   asserted), unlike a general resolve-worker.
3. **Checklist union algorithm.** Parse each side's `tasks.md` (via `git show
   :2:<path>`/`:3:<path>` for ours/theirs during the conflict) into task lines; for a
   task-checkbox line (`- [ ]`/`- [x]` prefix), the merged line is checked if either side
   checked it, using the line's text (post-checkbox) as the join key; non-checkbox lines
   and lines that don't have a corresponding entry on both sides pass through from
   whichever side has them (structural/prose edits union rather than conflict, since the
   scenario is specifically two sides both appending their own tasks' checkmarks to a
   shared structure). Write the resolved file, `git add`, `git commit --no-edit` to
   conclude the merge.
4. **Stamp `terminal_status` for `"failed"` in `_apply_step_commit` and `live_run()`,
   mirroring the existing `"escalated"` stamp exactly.** This is the minimal, root-cause
   fix: it corrects the one place the coordinator's computed status is discarded instead
   of being recorded, so every consumer of `report.terminal_status` (not just
   `clear_tasks()`) benefits, and it naturally covers every role that can reach
   `status: "failed"` through `dispatch.transition()` (`ROLE_IMPLEMENT`, `ROLE_FIX`,
   `ROLE_CLEANUP`), not only the `ROLE_FIX`-only case the brief described. Preferred over
   broadening `_terminal_failure()`'s own matching logic, which would leave every other
   `report.terminal_status` consumer with the same blind spot.

## Risks / Trade-offs

- The tasks.md union-merge touches the audit trail of which task was checked by which
  group's integration; a union can only add checkmarks, never remove one either side
  already had, so no completed work is ever hidden after the merge.
- Re-attempting the carry on every retained-worktree resume adds one `git fetch` in the
  common (already-healthy) case only when the first `_require_dependency_files` check
  actually raises -- no added cost on the hot path where nothing is missing.
