## Why

`shape_problems()` (`src/worktrail/conductor/parallelism.py:181-283`) computes the
critical-path/width ratio, the same-file dependent-chain length, and the missing-test-scope
check over `merged`'s fan-out tasks with only one filter applied: `kind not in TAIL_KINDS`
(`parallelism.py:196`). It never reads `status` — confirmed by grepping the function body —
so an already-`completed` task (both task formats set `status` to `"completed"`;
`worktrail.taskformats.openspec.schema.STATUS_COMPLETED`,
`worktrail.taskformats.devkit.schema.TaskStatus.COMPLETED`) still counts toward the critical
path, still occupies a slot in a same-file chain, and can still be flagged for missing test
scope, in every plan `_check_shape()` (`compile.py:487-499`) re-validates. `_check_shape` runs
"on every settled plan — seeded, cache-hit, and freshly compiled alike" (its own docstring),
which includes a **resumed** run: re-running `worktrail-compile` against a change with several
tasks already marked done re-derives the same merged task list from `apply_to_tasks` and
re-evaluates shape over the *original* task count, not the remaining one. A plan that was
correctly accepted at first compile can start failing the same gate later purely because
tasks finished — the opposite of the gate's purpose, which is to warn about the *work still
ahead*, not work already behind. Worse, a task-level status transition (marking task 4.1
done) can silently make a currently-in-flight run start raising `PlanShapeError` on its next
resume, with no code or plan change to explain why.

The only ranked fold candidate, `shared-pr-landing-pipeline`, is scoped to PR-landing
mechanics (compile marker, labels, CI watch) and explicitly disclaims changing unrelated
orchestration concerns — folding this in would conflate an unrelated conductor bug with that
change's own scope.

## What Changes

- `shape_problems()` excludes any task whose `status` is `"completed"` (either task format's
  done marker) from the fan-out set before computing critical path, width, same-file chains,
  and missing test scope — the gate evaluates the shape of *remaining* work only.
- A task with no `status` key (a format that doesn't set one, or an older cached plan) is
  treated as not completed, so this is purely a narrowing filter: a fully-pending plan (the
  case every existing test covers) is unaffected.
- Dependency edges into an excluded completed task are dropped the same way edges into an
  excluded tail-kind task already are (`by_id`'s `deps` filtered to `ids`), so a same-file
  chain is measured only across the tasks that still have to run.

## Capabilities

### Modified Capabilities

- `compile-plan-shape-gate`: the plan-shape gate (critical-path/width, same-file chain,
  missing-test-scope) now excludes tasks already marked `completed` before evaluating any of
  its three rules.

## Impact

- **Code**: `src/worktrail/conductor/parallelism.py` — `shape_problems()`'s fan-out filter.
- **Tests**: `tests/conductor/test_parallelism.py` — extend the `_task()` helper with a
  `status="pending"` default, and add coverage: a completed predecessor in an otherwise-serial
  chain no longer counts toward the critical-path rule, a completed writer no longer extends a
  same-file chain, and a completed task with no test scope is no longer flagged.
- **Non-goals**: changing what `status` values exist or how a `TaskSource` writes them; changing
  `profile()`/`estimate_minutes()` (already documented as advisory, not gated); changing
  `_check_shape()`'s call site in `compile.py` (the fix belongs entirely inside
  `shape_problems()`, which is the single place all three rules already share a fan-out list).
