## 1. Reconciled-task exemption

- [x] 1.1 In `src/worktrail/router/dashboard.py`, add `_is_reconciled_task()` (frontmatter `stale-sweep: exempt`, or `status: implemented` plus a `> **UI REMOVED|SUPERSEDED**` body marker), stamp a `reconciled` flag per row in `_load_tasks`, exclude reconciled rows from the `_pending_impl_stale` / `_pending_tail_stale` candidate lists, and count them as settled in `_count_tasks`; cover the marker, the opt-out, the pending-with-marker and implemented-without-marker negatives, and a reconciled task beside a stale sibling in `tests/router/test_dashboard.py`. (Requirement: Stale-bookkeeping check runs as a third independent per-repo check)
- [x] 1.2 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends on 1.1.

## 2. Folded from 20260831-060019-stale-bookkeeping-worktrail

- [ ] 2.1 openspec/changes/stale-sweep-reconciled-task-exemption exists and its stated modified capability ('stale-bookkeeping-sweep-check ... SHALL skip reconciled tasks') directly matches this brief's topic (stale bookkeeping drift findings); it is also the top-ranked candidate (score 0.43).
