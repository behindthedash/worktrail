## Why

`parallelism.shape_problems()` (`src/worktrail/conductor/parallelism.py:214-223`) returns early
when the pending fan-out list is empty, checking only for cleanup/verification mismatches. A
change whose `tasks.md` holds only tail-kind tasks (for example a single `[e2e]` task, or
`[e2e]` + `[docs]`) therefore compiles with exit 0 and a summary line of
`parallelism: 0 task(s), critical path 0, width 0`. Nothing is ever fanned out, yet every
consumer reads the exit code as "plan accepted": the `#compile-gate` step in
`skills/worktrail-go/references/subagent-prompts.md` only acts on a non-zero exit and on
`note:` lines, so the orchestrator launches, dispatches the tail, and the run "succeeds"
having done no implementation work. The plan-shape gate (#923) and the serial-DAG warning
(#887) never covered this case. (Work-queue brief
`20260918-081531-compile-zero-fanout-silent-accept`.)

## What Changes

- `shape_problems()` reports a problem line when the merged plan contains no fan-out task at
  all (every task is a `TAIL_KINDS` kind, or the plan is empty). The line names the tail task
  ids and tells the author to add at least one implementation task with `files:` scope, or to
  retag a tail task whose body is really implementation work. Existing behaviour is kept
  for a plan whose fan-out tasks exist but are all `status: completed` -- that is a legitimate
  re-compile mid-run and must not start failing.
- Because the rejection rides the existing `PlanShapeError` path, `worktrail-compile` exits 1
  in text and `--json` modes, writes no marker, and the orchestrator's pre-fan-out plan
  application refuses to launch -- no change to the `#compile-gate` bash is needed.
- The `#compile-gate` recovery table gains the zero-fan-out class so an operator or auto-mode
  run knows the fix is authoring, not a retry.

## Capabilities

### New Capabilities

### Modified Capabilities
- `compile-plan-shape-gate`: adds a fourth rule -- a plan with no fan-out task is rejected at
  compile rather than accepted with a zero-task summary.

## Impact

- `src/worktrail/conductor/parallelism.py` (`shape_problems`, early-return branch).
- `tests/conductor/test_parallelism.py`, `tests/conductor/test_compile.py` (new regression
  tests; the existing `test_cleanup_mismatch_fires_even_when_fanout_is_empty` now also
  expects the zero-fan-out line).
- `skills/worktrail-go/references/subagent-prompts.md` (`#compile-gate` table row).
- No CLI flag, policy key, or on-disk format change.
