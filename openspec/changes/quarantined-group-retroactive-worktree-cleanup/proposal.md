## Why

`orchestrator/verify.py`'s `cleanup_group()` tears down a group's task and verify
worktrees only on the delivered-merge path (`verify.py:1884` and `:1917`). A group
that quarantines instead deliberately keeps its worktrees for human review -- the
right call at that moment. But nothing ever comes back to them: once a human lands
the quarantined group's PR (or a later re-run subsumes its files, which
`quarantine-reconciliation` already detects), the `<spec_id>-<task>` and
`<spec_id>-verify-<group>` checkouts stay on disk indefinitely.

`router/sweep_stale_worktrees.py` names this gap in its own docstring and cannot
close it today: task-leaf branches are never pushed on their own, and a squash-merged
group PR gives their commits a different patch-id, so `git cherry` reports them
UNMERGED, `ls-remote` finds no remote branch, and the sweep files them under
`UNPUSHED -- cannot confirm safe to reclaim`. The evidence that would settle it lives
in the run journal (`groups[name].pr_url` / `state`) and the cached RunPlan
(group -> task ids), and the sweep does not read either.

## What Changes

- Teach `sweep_stale_worktrees.py` to attribute each `<spec_id>-*` worktree to its
  run-journal group (task worktrees via `plan_groups()` over the cached RunPlan, the
  same recomputation `quarantine_selfcheck.py` uses; verify worktrees by name) and, for
  a `QUARANTINED` group whose PR is confirmed merged or whose finding
  `quarantine-reconciliation` already auto-resolves, classify its clean worktrees as
  reclaimable under a new `QUARANTINE-MERGED` state that names the evidence.
- Expose the group -> task-id recomputation from `quarantine_selfcheck.py` so the
  sweep reuses it instead of re-implementing the RunPlan lookup.
- Extend `worktrail-go/references/worktree-cleanup.md`'s classification step so the
  attended `cleanup-worktrees` flow uses the sweep's JSON output as its journal-aware
  bucket source, and prunes `QUARANTINE-MERGED` worktrees through its unchanged
  confirm -> liveness-guard -> remove sequence.

The sweep stays report-only and the existing DIRTY / UNPUSHED keep rules still win:
retroactive teardown is a classification the attended flow acts on, never an
unattended delete.

## Capabilities

### New Capabilities

- `quarantined-worktree-retroactive-reclaim`: journal-aware attribution of orchestrator
  worktrees to quarantined groups, the merge-evidence rule that flips them to
  reclaimable, and the attended cleanup path that consumes it.

### Modified Capabilities

None. `quarantine-visibility` / `quarantine-reconciliation` are reused as-is (their
`reconcile_finding()` is one of the two accepted merge-evidence signals);
`worktree-deletion-liveness-guard` continues to gate the actual removal.

## Impact

- `src/worktrail/router/sweep_stale_worktrees.py` (classification + docstring gap note),
  `src/worktrail/router/quarantine_selfcheck.py` (public group -> task-id helper).
- `tests/router/test_sweep_stale_worktrees.py`, `tests/router/test_quarantine_selfcheck.py`.
- `skills/worktrail-go/references/worktree-cleanup.md` (classification step).
- No change to `verify.py`'s `cleanup_group()` gate, the run-journal schema, the sweep's
  CLI/exit-code contract, or the plugin surface.
