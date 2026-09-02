## Why

The stale-bookkeeping detector (`_pending_impl_stale` / `_pending_tail_stale` in `dashboard.py`, reused by the weekly `worktrail-spec-sync-sweep`) treats every non-`completed` devkit task whose `files:` are all git-tracked on base as stale bookkeeping. A task deliberately parked at `status: implemented` because the work it shipped was later removed or superseded — GGB `026-authenticated-feedback-capture/.../TASK-CHG-003.md`, which carries an explicit `> **UI REMOVED (PR #576)**` reconciliation note explaining why its ACs can no longer be verified and the DoD gate refuses `completed` — matches that rule too, so every sweep re-files a "confirm & close" Drift Brief for it (brief 20260831-060017, re-verified 2026-09-01). The operator has already reconciled the task; the detector has no way to know.

## What Changes

- Recognise a *reconciled* devkit task: `status: implemented` whose body opens with a `> **UI REMOVED …**` or `> **SUPERSEDED …**` blockquote marker, or any task carrying the explicit frontmatter opt-out `stale-sweep: exempt`.
- Exclude reconciled tasks from both stale-bookkeeping candidate lists (impl and tail) so no sweep or interactive `/go` scan re-flags them.
- Count reconciled tasks as settled in the dashboard's task tally so a spec whose only remaining non-completed task is reconciled is neither bounced onto the orchestrator path nor reported as stale.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `stale-bookkeeping-sweep-check`: the reused detection SHALL skip reconciled tasks.

## Impact

- `src/worktrail/router/dashboard.py`: `_load_tasks` gains a `reconciled` flag per row; `_count_tasks`, `_pending_impl_stale`, `_pending_tail_stale` honour it.
- `tests/router/test_dashboard.py`: regression coverage for the marker, the opt-out, the pending-with-marker and implemented-without-marker negatives, and a reconciled task beside a genuinely stale sibling.
- OpenSpec-format changes are unaffected (`tasks.md` checkboxes have no `status: implemented` state).
