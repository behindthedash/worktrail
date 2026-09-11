## Why

Nightly `bridge-health-guard` checks have continued to report one to five
non-terminal `/go` run records with no heartbeat for more than two hours on
most days from 2026-08-22 through 2026-09-06. The records span several repos
and stages (`route_selected`, `executing`, `validating`, and
`state_restored`), so this is a recurring observability problem rather than a
single historical backlog.

The known native-skill double-start cause is already fixed by PR #591
(`9dc9daaa`): `worktrail-go` passes `run:$RUN`, and
`worktrail-sdd-workflow` reuses that record. Reapplying that change would not
explain or safely resolve the later findings.

The current `worktrail-run-record liveness` result equates an old
`updated_at` heartbeat with a stale owner. That is only an activity signal: a
long-running `worktrail-detach` child can still be alive while no run-record
mutation occurs. `sweep-orphans` consumes the same signal and can therefore
close a still-running detached orchestration as `failed_terminal`. Conversely,
a stale heartbeat with no process evidence cannot distinguish a dead dispatch
from an unattended native-session run. The health check needs that distinction
before it can be treated as evidence of a dead dispatch.

## What Changes

- Record the detached-launch handle that owns a run when the workflow launches
  `worktrail-live full-real` through `worktrail-detach`.
- Make `worktrail-run-record liveness` report heartbeat freshness and detached
  process state as separate evidence, with a conservative reconciliation
  classification instead of treating an old heartbeat as proof of death.
- Change `sweep-orphans` to close only runs whose stale heartbeat is paired
  with confirmed terminated detached-process evidence. It will leave a
  running detached process and a record without usable process evidence
  non-terminal, reporting each separately for an operator or Route E to
  resolve.
- Update the workflow instructions and their plugin-surface coverage so the
  detach handle is bound to the already-open run record immediately after a
  successful launch.

## Capabilities

### New Capabilities

- `run-record-liveness-reconciliation`: distinguishes heartbeat freshness from
  observable detached-process liveness and makes automatic orphan
  reconciliation safe in the presence of a still-running detached owner.

### Modified Capabilities

(none — no current OpenSpec capability specifies run-record liveness or
orphan-sweep behavior.)

## Impact

- `src/worktrail/router/run_record.py` — persisted detached-owner metadata,
  liveness classification, and conservative orphan-sweep eligibility.
- `skills/worktrail-go/references/subagent-prompts.md` — bind a successful
  `worktrail-detach launch` handle to the run that initiated it.
- `tests/router/test_run_record.py` and `tests/test_plugin_surface.py` —
  hermetic process-state and workflow-contract coverage.

Out of scope: changing native-skill dispatch ownership, adding a background
heartbeat writer, bulk-closing the existing backlog, or declaring a
no-detached-handle record dead. Those cases remain deliberately recoverable by
the existing Route E reconstruction procedure.
