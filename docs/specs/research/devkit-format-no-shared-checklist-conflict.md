# Investigation: does devkit task format have an analog of the tasks.md checklist-union carry exception?

Route I investigation, brief `20260820-010811-the-new-tasks-md-checklist`.
Investigation complete; no analogous failure mode found, no fix needed. Stop
(does not continue into Route F).

## Verified Observations

- PR #554's `_resolve_tasks_md_checklist_conflict` (`live.py:1704`) is
  hardcoded to one exact path: `openspec/changes/<spec_id>/tasks.md`
  (`live.py:1712`, `expected = f"openspec/changes/{spec_id}/tasks.md"`). It
  only engages when the conflicted-file set from
  `_carry_squash_merged_dependencies`'s merge is *exactly* `[expected]`
  (`live.py:1713`, `if unmerged != [expected]: return False`) — confirmed by
  `test_conflict_touching_tasks_md_and_another_file_still_aborts`
  (`tests/orchestrator/test_stacked_worktree_squash_carry.py:312`), which
  asserts a conflict spanning tasks.md *and* any other file still aborts.
- The reason this conflict is routine and safe to auto-resolve for OpenSpec:
  every task in an OpenSpec change shares **one** `tasks.md` checklist file.
  Each concurrently-integrating task group independently checks off its own
  task's box in that same shared file; a squash-merge loses the common
  ancestor, so git can't three-way-merge the checkbox edits and reports a
  content conflict even though the two sides never touched each other's
  content in a meaningful way. `_union_merge_checklist` (`live.py:1660`)
  resolves it by taking the OR of each side's checkmark, keyed by line text,
  never un-checking anything either side already completed.
- The devkit task format has no equivalent shared file. Each task's
  completion state — `status:` frontmatter plus body-section checkboxes —
  lives in its own dedicated file, `docs/specs/<id>/tasks/TASK-<n>.md`
  (`taskformats/devkit/schema.py`'s `read_task_file`/`write_task_file`,
  `taskformats/devkit/source.py:189`'s `_find_tasks_dir`). Task completion is
  written via `DevkitTaskSource.mark_status()` (`source.py:400`), which loads
  and writes exactly one file: `path = self.task_file_path(task_id, spec_ref)`
  (`source.py:414`, `write_task_file(path, frontmatter, body)` at
  `source.py:428`) — no aggregate/index/checklist file is ever touched.
- Two different tasks' completion writes therefore always land in two
  different files (`TASK-A.md` vs `TASK-B.md`). Since
  `_carry_squash_merged_dependencies`'s merge (`live.py:1788`) is
  format-agnostic — it merges git branches, not spec content — a devkit
  spec's dependency-carry merge can only conflict on a file both the current
  task's worktree and the stale dependency's base content actually collide
  on. With devkit's one-file-per-task layout, that can only be a real shared
  source/content file the two tasks legitimately both touch, never a
  bookkeeping file analogous to `tasks.md`.
- The only other cross-task shared artifact in either format is
  `knowledge-graph.json` (`docs/specs/<id>/knowledge-graph.json`). It is
  written exactly once per orchestrator run, by a single dedicated sync
  worktree, only *after* every group has integrated, verified, and merged
  (`skills/worktrail-go/references/subagent-prompts.md#sync-before-teardown`,
  steps 2–4) — not concurrently by in-flight task workers during the run. It
  therefore never participates in `_carry_squash_merged_dependencies`'s
  per-task-worktree merge at all, in either format.

## Confirmed Root Cause (of the original question, not a defect)

Devkit format does **not** have an analogous shared-state-file
squash-merge-carry-conflict failure mode. The failure mode PR #554 fixed is
specific to OpenSpec's single-shared-checklist-per-change design; devkit's
per-task-file design has no file that two concurrently-integrating task
groups both write as a side effect of marking their own task complete, so
the "routine, safely-resolvable add/add conflict" shape cannot arise for a
devkit-format spec by construction.

A conflict `_carry_squash_merged_dependencies` hits while carrying a
squash-merged, branch-gone dependency into a devkit-format task's worktree is
therefore always a genuine content collision on a real file the two tasks
both touch — exactly the case the pre-existing abort + `WARN:` fallback
(`live.py:1790`-`1797`) is meant to catch. A union-merge equivalent would be
actively wrong there: it would risk silently discarding one side's real,
conflicting code changes rather than surfacing them.

## Recommended Next Route

None. No fix is needed — devkit format's existing abort + WARN behavior on a
dependency-carry conflict is already correct for the only class of conflict
that can occur there.
