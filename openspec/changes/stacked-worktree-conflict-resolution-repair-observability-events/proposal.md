## Why

PR #554 (`resume-repair-and-checklist-carry`) added two orchestrator self-healing
repair paths in `live.py`: retained-worktree squash-merge-carry repair on resume
(`_require_dependency_files_with_repair`), and tasks.md checklist-union conflict
resolution (`_resolve_tasks_md_checklist_conflict`). Both currently only print a
`WARN:`/log line when they engage -- neither fires a structured journal event the
way the analogous `dependency_file_drift` event does, and `safety_net_report.py`
has no aggregation for either. This exact observability gap is why brief
20260817-223443 needed TWO separate live incidents (2026-08-17 and 2026-08-19)
weeks apart before anyone connected the pattern -- a single run's transcript
showed the WARN, but nothing let a later review ask "how often does this recur,
and on which tasks/specs" without grepping raw logs across every run journal.

## What Changes

- `_carry_squash_merged_dependencies` returns whether it resolved a checklist
  conflict via the union-merge path (previously void), so callers can journal it.
- `_require_dependency_files_with_repair` fires a structured `worktree_drift_repaired`
  journal event when the repair retry (re-running
  `_carry_squash_merged_dependencies` after a `WorktreeMissingDependencyFileError`)
  resolves the drift, mirroring how `_require_dependency_files` already fires
  `dependency_file_drift`.
- `_carry_squash_merged_dependencies` fires a structured `checklist_conflict_resolved`
  journal event when `_resolve_tasks_md_checklist_conflict` resolves a
  tasks.md-only merge conflict via the checklist union.
- `safety_net_report.py`'s `scan()`/`render()` aggregate both new event types the
  same way `dependency_file_drift` is aggregated (fire counts, breakdown by task).
- PR #554's third change (stamping `terminal_status` for a normal "failed"
  transition report) is a one-time correctness fix to existing status
  recognition, not a recurring silent self-healing path -- it is out of scope
  for this change; see the design doc for the reasoning.

## Capabilities

### Modified Capabilities
- `stacked-worktree-conflict-resolution`: the retained-worktree repair retry and
  the tasks.md checklist-union carry exception each fire a structured,
  cross-run-aggregable journal event when they engage, instead of only a
  single-run `WARN:` log line.

## Impact

- `src/worktrail/orchestrator/live.py`: `_carry_squash_merged_dependencies`,
  `_require_dependency_files_with_repair`.
- `src/worktrail/orchestrator/safety_net_report.py`: `events_in_journal`, `scan`, `render`.
- Tests: `tests/orchestrator/test_stacked_worktree_squash_carry.py` (or the module
  PR #554 added its regression tests to), `tests/orchestrator/test_safety_net_report.py`.
