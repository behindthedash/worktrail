## Why

The drain's `sync-pending` remediation currently treats the two supported
spec formats as if they shared a sync operation. `find_sync_pending_specs()`
does not retain the dashboard row's format, `build_sync_command()` always
dispatches `/opsx:sync`, and `_run_sync_pending()` has no format branch.

`/opsx:sync` reconciles an OpenSpec change's delta specs with
`openspec/specs/`; it has no devkit `docs/specs/<id>/knowledge-graph.json`
work to perform. Consequently, a devkit finding can exit successfully with
no diff, remain `sync-pending` because its knowledge graph lacks a
`metadata.analysis_sources` entry from the `spec-sync` agent, and be selected
again by every drain sweep. The queue-triage evidence recorded a merged empty
PR from this path (datalena #2669); datalena #2699's manual remediation shows
the required knowledge-graph shape.

## What Changes

- Carry each sync-pending finding's declared dashboard format, defaulting a
  format-less row to `devkit`, as the stale-bookkeeping finder already does.
- Select the sync dispatch by that format: active OpenSpec changes continue to
  use `/opsx:sync <change-id>`; devkit specs invoke the developerkit
  spec-sync agent against `docs/specs/<spec-id>`, which reconciles the spec
  with merged code and writes the `spec-sync` analysis source to
  `knowledge-graph.json`.
- When a successful sync dispatch produces no worktree diff, re-check the
  finding. If it is still `sync-pending`, fail that remediation instead of
  reporting a successful no-op; a finding that disappeared concurrently may
  still finish as a no-op. The existing sweep's per-finding failure isolation
  continues with other findings.
- Add regression coverage for format propagation, both dispatched command
  shapes, and the persistent-no-diff failure guard.

## Capabilities

### Modified Capabilities

- `drain-stage-remediation-table`: makes the existing sync-pending remediation
  format-aware and requires it to surface a successful-but-still-pending
  no-diff result as a remediation failure.

## Impact

- `src/worktrail/drain/drain.py` (`find_sync_pending_specs`,
  `build_sync_command`, `_run_sync_pending`, and their sync-pending logging
  and result handling)
- `tests/drain/test_drain.py`
- No changes to dashboard stage detection, the OpenSpec sync skill, the
  shared remediation-table engine, or PR landing behavior.
