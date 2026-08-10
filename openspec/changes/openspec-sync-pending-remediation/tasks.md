## 1. OpenSpec sync-pending stage

- [x] 1.1 In `src/worktrail/router/dashboard.py`, implement
      "Reconciliation is deterministic and read-only" with a parser/comparator
      for OpenSpec delta requirement, scenario, removal, and rename headings;
      implement "OpenSpec delta reconciliation stage" by classifying a
      task-complete unsynced change as `sync-pending`, and implement
      "Verification takes precedence over synchronization" by placing that
      classification after the existing verify-pending check.
- [x] 1.2 In `tests/router/test_dashboard.py`, cover unsynced and reconciled
      deltas, a change with no deltas, and verify-pending precedence.

## 2. Existing drain-row integration

- [x] 2.1 In `tests/drain/test_drain.py`, cover the modified
      "Sync-pending remediation" requirement: prove
      `find_sync_pending_specs()`
      discovers an unsynced OpenSpec change as
      `openspec/changes/<change-id>` and stops discovering it once canonical
      specs contain the declared structure; do not change the finder/action/table.

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_dashboard.py
      tests/drain/test_drain.py`.
- [ ] 3.2 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`.
- [x] 3.3 [cleanup] Run `openspec validate
      openspec-sync-pending-remediation --strict` and confirm the change is
      structurally valid.
