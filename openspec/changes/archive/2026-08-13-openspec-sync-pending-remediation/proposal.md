## Why

The sync-pending drain remediation added by `drain-sync-pending-remediation`
already scans through `dashboard.scan()`, and that scan includes active OpenSpec
changes. The missing link is stage classification: `_safe_detect_openspec()`
reports every task-complete, verify-complete change as `complete`, even when its
delta requirements have not been merged into `openspec/specs/`. Consequently the
finder can never select an OpenSpec change for the existing `/opsx:sync` action.

## What Changes

- Add a read-only OpenSpec delta reconciliation check to the dashboard.
- Report a task-complete OpenSpec change as `sync-pending` when its delta is not
  yet reflected in the canonical capability specs; keep `verify-pending` higher
  priority.
- Preserve `complete` for a change whose delta is already reconciled, making the
  existing sync action idempotent across drain sweeps.
- Add focused dashboard and drain-finder regression tests. The existing finder,
  action, remediation table, and summary shape remain unchanged.

## Capabilities

### New Capabilities
- `openspec-sync-pending-detection`: deterministic, read-only reconciliation of
  active OpenSpec deltas against canonical specs for dashboard stage selection.

### Modified Capabilities
- `drain-stage-remediation-table`: clarifies that the existing sync-pending row
  covers both devkit and OpenSpec findings emitted by the common dashboard scan.

## Related Change

This is a deliberately separate, narrow follow-up to
`drain-sync-pending-remediation`. It does not widen or rewrite that merged
change's finder/action/table work; it supplies the OpenSpec stage signal that
the existing finder was designed to consume.

## Impact

- `src/worktrail/router/dashboard.py`: OpenSpec delta reconciliation and stage.
- `tests/router/test_dashboard.py`: stage semantics and priority coverage.
- `tests/drain/test_drain.py`: OpenSpec finding reaches the existing sweep.
- `openspec/specs/drain-stage-remediation-table/spec.md`: updated only by the
  normal sync/archive lifecycle, not by implementation code.

