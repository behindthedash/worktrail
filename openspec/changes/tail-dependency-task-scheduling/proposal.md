## Why

The main scheduler intentionally excludes `e2e` and `cleanup` tail tasks until its
implementation pass ends. A non-tail task that depends on one of those tail tasks can
therefore enter neither pass: its dependency cannot complete during the main pass, and
the tail pass never considers the non-tail task. The run can exit successfully while
reporting fewer completed tasks than planned.

The existing `tail-dispatch-require-merged-deps` change only holds tail-kind tasks for
non-merged implementation dependencies; it does not address this inverse dependency
shape. Rejecting the unschedulable plan before a live run prevents silent skipped work.

## What Changes

- Detect a pending non-tail task that declares a dependency on an `e2e` or `cleanup`
  task as an unschedulable tail-dependency inversion.
- Make live-run precheck report each inversion by task id and exit non-zero, alongside
  its existing plan-shape diagnostics.
- Make the run-plan compilation validation reject the same shape so `worktrail-compile`
  and a live launch apply the same scheduling contract.
- Add regression coverage for the incident shape and for valid tail-to-tail and
  implementation-only dependencies.

## Capabilities

### New Capabilities

- `orchestrator-tail-dependency-validation`: validation rejects task graphs in which
  non-tail work depends on a tail-kind task that the scheduler defers.

### Modified Capabilities

<!-- None. -->

## Impact

- `src/worktrail/conductor/`: run-plan shape validation gains a tail-dependency
  inversion check.
- `src/worktrail/orchestrator/live.py`: precheck surfaces the validation failure before
  a live run starts.
- `tests/conductor/` and `tests/orchestrator/`: focused plan-validation and CLI precheck
  regression tests.
