## Why

The resume dashboard's `_find_worktrees` (`src/worktrail/router/dashboard.py`) lists
only the direct children of `<repo>-worktrees/` that are themselves git checkouts.
The orchestrator places per-task worktrees one level deeper, inside a non-git
container directory (`<repo>-worktrees/<spec>-worktrees/<task>/`). Those are never
reported, so the dashboard never offers the `cleanup-worktrees` action for them.
Observed on `gracefully-giving-back`: five registered worktrees under
`030-maintenance-gate-isr-spec-worktrees/maintenance-gate-isr-{1.1,1.2,2.1,3.1,4.1}`
stayed invisible long after their work ended (work-queue brief
`20260917-102831-gracefully-giving-back-stale-task`).

## What Changes

- `_find_worktrees` descends one level into each direct child of `<repo>-worktrees/`
  that is a directory but not a git checkout, and reports the git checkouts found there.
- The `worktrees` names reported by `scan_repos` and the single-repo dashboard are
  paths relative to `<repo>-worktrees/` (`<container>/<task>` for nested ones;
  unchanged bare name for direct children).
- One-off operator step: tear down the five stale `gracefully-giving-back` worktrees
  through the normal `cleanup-worktrees` flow once they are visible.

## Capabilities

### New Capabilities
- `dashboard-nested-worktree-scan`: how the dashboard discovers worktrees under
  `<repo>-worktrees/`, including those nested in a per-spec container directory.

### Modified Capabilities

None.

## Impact

- `src/worktrail/router/dashboard.py` (`_find_worktrees` and its two callers).
- `tests/router/test_dashboard.py`.
- Dashboard output: repos with nested task worktrees now show a worktree cleanup line.
