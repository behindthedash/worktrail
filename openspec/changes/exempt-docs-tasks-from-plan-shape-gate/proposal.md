## Why

`worktrail-compile` rejects a plan whose pending `docs`-kind tasks form an ordinary
documentation chain. `parallelism.shape_problems()` builds the fan-out it judges with
`kind not in TAIL_KINDS` (`src/worktrail/conductor/parallelism.py:218-221`), and `TAIL_KINDS`
is only `{"e2e", "cleanup"}` (`src/worktrail/orchestrator/coordinator.py:49`), so `docs`
tasks still count toward the serial and same-file rules even though every other consumer
treats that kind as non-implementation work: `live.py`'s `_review_exempt` skips review for it,
`live.py:1289` and `live.py:1444` exempt it alongside `e2e`/`cleanup`, `compile.py`'s scope-gap
remediation groups it with `cleanup`, and `shape_problems` itself already excludes it from the
missing-test-scope rule (`parallelism.py:300`) and from the no-fan-out rule
(`parallelism.py:226`).

Reproduced with a merged plan shaped like the reported one -- a completed implementation task
followed by three pending `docs` tasks, each depending on the previous and each declaring the
change's own `openspec/changes/<id>/tasks.md`:

```
serial: critical path 3 exceeds max(width 1, 2): 6.1 -> 6.2 -> 6.3
same-file chain: 6.1 -> 6.2 -> 6.3 all declare openspec/changes/x/tasks.md (3 > 2)
```

Retagging those same three tasks `cleanup` returns `[]`. The docs chain is bookkeeping: its
edges come from `tasks.md` file order and the shared file is the task list itself, not an
implementation artifact. The gate is behaving as written, but
`openspec/specs/compile-plan-shape-gate/spec.md` never states that docs is subject to these
rules, while its sibling rules already exempt it. (Work-queue brief
`20261125-033557-plan-shape-gate-rejects-docs`.)

The separate no-fan-out rejection (archived change `compile-fail-loud-on-zero-fanout`, #1248)
is deliberate and is preserved: a docs-only plan must still fail.

## What Changes

- `shape_problems()` excludes `docs`-kind tasks from the fan-out the serial and same-file
  rules evaluate, alongside `TAIL_KINDS`, through one named exemption set. A dependency edge
  through a docs task no longer bridges two implementation tasks into a serial chain.
- The no-fan-out rule keeps its existing treatment of `docs` (already non-implementation): a
  docs-only plan is still rejected, while a plan whose implementation tasks are `completed`
  and whose only pending tasks are docs passes, like the completed-tail re-compile case.
- `profile()` is unchanged: it is the advisory wall-clock signal, and docs tasks are dispatched
  and consume real time.
- Tests cover the reported shape, the preserved no-fan-out rejection, and the still-firing
  implementation rules with docs tasks present.

Not changed: no policy key, no task-format change, and no authoring guidance -- the
`compile_max_*` thresholds and the `files:`/same-file-folding guidance already exist.

## Capabilities

### New Capabilities

None.

### Modified Capabilities
- `compile-plan-shape-gate`: the serial and same-file rules evaluate implementation tasks
  only, excluding `docs`-kind tasks as they already exclude `e2e`/`cleanup`.

## Impact

- `src/worktrail/conductor/parallelism.py`: one exemption set used by the gate fan-out and the
  existing no-fan-out check; docstring/comment updates.
- `tests/conductor/test_parallelism.py`: regression coverage for the reported shape and the
  preserved boundaries.
- No CLI flag, policy key, task-format, dispatch, or on-disk change.
