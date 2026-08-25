# Proposal: Repair stale resumed worktrees, safely carry checklist merge conflicts, and recognize fix-role failures as clearable

## Why

Two related classes of orchestrator resume failures required manual git intervention
during live runs, both traced to `src/worktrail/orchestrator/live.py`:

1. **Retained worktree never repairs on drift.** `ensure_wt`'s (`live_run_real`,
   live.py:3060) and `_ensure_wt`'s (`_pipeline_scheduler`, live.py:4199) `else` branch
   -- taken whenever a task's worktree already exists on resume -- only re-validates
   dependency content via `_require_dependency_files`; it never re-attempts the carry
   that `add_stacked_worktree` runs on first creation. Once a worktree hits
   `WorktreeMissingDependencyFileError` because a dependency squash-merged *after* the
   worktree was created, every subsequent resume re-hits the identical error forever --
   observed live on run go-20260817-204002 (change stuck-remediation-detector) and again
   go-20260819-210542 (devops, group feature-1, tasks 1.2/2.1/3.1), the latter confirming
   this is the normal happy-path timing (a foundation group finishing first), not a rare
   edge case. Manual recovery both times required raw `git worktree remove --force` +
   `git branch -D`, or hand-cherry-picking commits onto a fresh worktree.

2. **Squash-merge carry conflicts always fail loud, even for the change's own
   always-safely-mergeable tasks.md checklist.** `_carry_squash_merged_dependencies`
   (live.py:1656) intentionally never auto-resolves a merge conflict --
   `openspec/specs/stacked-worktree-conflict-resolution/spec.md`'s "Carry already-merged
   dependency content..." requirement mandates failing loud on any genuine content
   conflict, a deliberate safety fix after an earlier `-X ours` auto-resolve was found to
   silently discard live-base content
   (`docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md`). But the
   second live occurrence's actual conflict was confined to the change's own
   `openspec/changes/<id>/tasks.md` -- a checklist file where each concurrently-merged
   group independently checks off its own tasks, so squash-merge history loses the
   common ancestor and produces an add/add conflict that is always safely resolvable by
   taking the union of checked boxes. The blanket fail-loud path quarantined a group with
   correct implementation work stranded in per-task worktrees, requiring hand
   reconciliation.

Separately, found while recovering from (1): `clear_tasks()`'s `_terminal_failure()` gate
(live.py:1169) only recognizes a journal entry whose `report.terminal_status` is
`"failed"`/`"escalated"`, but `_apply_step_commit` (the production journal-entry-
construction path) only ever stamps `terminal_status` for the `"escalated"` outcome -- a
normal `fix`-role (or `implement`/`cleanup`-role) worker report with `status: "failed"`
transitions the task to `"failed"` but the entry never gets a `terminal_status` key at
all. `worktrail-live clear-task` then refuses with "no failed/escalated journal entries"
for a task `worktrail-live status` itself reports as `failed`, forcing a hand-patched
journal to unblock a resume.

## What Changes

- `ensure_wt`/`_ensure_wt`'s retained-worktree branch: on
  `WorktreeMissingDependencyFileError` from the first `_require_dependency_files` check,
  re-attempt `_carry_squash_merged_dependencies` once (idempotent -- no-ops via its own
  `merge-base --is-ancestor` check when nothing changed) and re-validate; only re-raise
  if the repair attempt didn't resolve it. No full worktree recreation -- the branch and
  any in-progress work stay intact.
- `_carry_squash_merged_dependencies`: before the general fail-loud abort, special-case a
  merge conflict confined entirely to the change's own
  `openspec/changes/<change_id>/tasks.md` -- resolve deterministically by taking the
  union of checked (`- [x]`) task lines from both sides, commit, and continue. Any
  conflict touching any other file (alone or in addition to tasks.md) still aborts and
  fails loud exactly as today; this is a narrow, deterministic exception, not a general
  auto-resolve.
- `_apply_step_commit` (and the mirrored `live_run()` demo-path entry construction)
  stamps `report_fields["terminal_status"]` for the `"failed"` transition outcome the
  same way it already does for `"escalated"`, so `clear_tasks()` recognizes any role's
  normal terminal failure, not only the review circuit-breaker.

## Capabilities

### Modified Capabilities

- `stacked-worktree-conflict-resolution`: adds a requirement that a retained
  (already-existing) task worktree missing dependency content on resume gets one repair
  attempt before failing loud; narrows the existing squash-merge-carry fail-loud
  requirement with a deterministic tasks.md-checklist-only exception.

## Impact

- `src/worktrail/orchestrator/live.py`: `ensure_wt`, `_ensure_wt`,
  `_carry_squash_merged_dependencies`, `_apply_step_commit`, `live_run()`'s `drive()`
  closure.
- `tests/orchestrator/`: new regression coverage for retained-worktree repair-on-drift,
  tasks.md checklist-conflict carry, and terminal_status stamping on a normal failed
  report (no owning spec -- an implementation-detail journal-classification fix bundled
  into this change since it was found while recovering from gap 1's incident).
- No CLI surface changes; no journal schema changes beyond the existing `terminal_status`
  field now being populated in more cases (additive, backward compatible -- its absence
  was never load-bearing for anything other than the exact classification gap being
  fixed).
