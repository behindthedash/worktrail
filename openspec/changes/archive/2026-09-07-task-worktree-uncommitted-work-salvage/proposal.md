## Why

Worktrail already knows that discarding a worker's uncommitted work is a real
loss: `VerifyRunner._salvage_uncommitted` (`src/worktrail/orchestrator/verify.py:464`,
called at `verify.py:881`) commits and pushes whatever a timed-out verify worker
left modified, because on 2026-09-02 a ci-fix worker's completed `ruff format`
sat uncommitted in the verify worktree while its group headed to quarantine for
that exact formatting failure.

Task worktrees get no such protection. `WorktreeManager.remove`
(`src/worktrail/orchestrator/worktree.py:156-161`) defaults `force=True` and
appends `git worktree remove --force` unconditionally — no `git status
--porcelain` check, no warning, no record. A task worker that produced real
edits but died, timed out, or exited before committing has that work deleted
silently at teardown (`verify.py:1678`), and nothing in the run's output tells
anyone it happened. The evidence is gone before a human could even know to look
for it.

## What Changes

- Before removing a task worktree, `WorktreeManager.remove` inspects the
  worktree for uncommitted tracked modifications and refuses to silently
  discard them.
- When such changes are found, they are preserved as a commit on the task's own
  branch rather than deleted, mirroring the verify-worker salvage path's
  intent (tracked modifications only — worker scratch such as
  `.claude/tsc-cache/` is never swept in).
- The salvage is recorded on the run's log so an operator can see that a task
  worktree held unexpected uncommitted work and where it now lives.
- Salvage is best-effort: a salvage failure never blocks or masks teardown, the
  same contract `_salvage_uncommitted` already holds for verify workers.

## Capabilities

### New Capabilities
- `task-worktree-uncommitted-work-salvage`: the pre-teardown inspection,
  preservation, and reporting contract for uncommitted work left in a task
  worktree.

### Modified Capabilities
(none — no existing requirement changes; this adds a guard in front of an
existing teardown step whose successful, clean-worktree behavior is unchanged)

## Impact

- **Modified code**: `src/worktrail/orchestrator/worktree.py`
  (`WorktreeManager.remove` and a salvage helper), plus `tests/orchestrator/`
  coverage alongside the existing `test_worktree_extras.py`.
- **Behavior preserved**: a clean task worktree is removed exactly as it is
  today; teardown still succeeds even when salvage fails.
- **Out of scope**: the ephemeral integration/verify worktrees created and torn
  down inside `integrate.py` and `verify.py`'s group cleanup (those are
  orchestrator-owned scratch trees, not worker output); changing
  `_salvage_uncommitted` in `verify.py`; pushing salvaged task-branch commits
  to the remote (task branches are integrated locally by `integrate.py`);
  salvaging untracked files.
