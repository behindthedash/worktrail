## Why

The `openspec` CLI (`@fission-ai/openspec`) downgrades its own
`ArchiveBlockedError` (incomplete tasks) to a stdout warning and proceeds
when `-y` is passed, so `drain.py`'s unattended `archive_openspec_change`
sweep currently has no independent check before invoking
`openspec archive -y <change-id>`. `dashboard.py`'s `_safe_detect_openspec`
only ever reports `stage == "complete"` once every task is already checked,
so this path is not reachable with unchecked tasks under correct stage
detection today — but the sweep runs unattended, nightly, with no human in
the loop to catch a stage-detection regression before it reaches the
archive step. Defense-in-depth belongs in the action itself, not solely in
the finder that feeds it.

## What Changes

- Before `archive_openspec_change` ever invokes `openspec archive` in the
  worktree, parse the change's `tasks.md`
  (`worktrail.taskformats.openspec.schema.parse_tasks_md`) and refuse — raise,
  with no `archive`/commit/push/PR attempted — if any task's status is not
  `completed`.
- Scope: `src/worktrail/drain/drain.py` only, specifically the
  `archive_openspec_change` / `_run_openspec_archive` path used by the
  `REMEDIATION_TABLE`'s complete-stage OpenSpec archive remediation. Does
  **not** touch `src/worktrail/router/close_stale_openspec.py`, which is a
  separate, narrower, already-adequate exception path.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `drain-stage-remediation-table`: the "OpenSpec change archive remediation"
  requirement gains a hard-refuse pre-check on `tasks.md` before any
  `openspec archive` invocation, independent of upstream stage detection.

## Impact

- **Code**: `src/worktrail/drain/drain.py` (`_run_openspec_archive`).
- **Tests**: `tests/drain/test_drain.py` gains a regression test proving
  `archive_openspec_change` raises — without ever invoking
  `openspec archive` — when a finding's `tasks.md` has an unchecked task.
- **Behavior**: purely additive safety check on an already-existing action;
  no change to the finder, the PR-opening path, or any other remediation row.
