## ADDED Requirements

### Requirement: Plans with no fan-out task are rejected at compile
The compile step SHALL reject a merged plan that contains no fan-out task -- every task has a
kind in `TAIL_KINDS` (`e2e`, `cleanup`, `docs`), or the plan has no tasks at all -- instead of
returning only the cleanup-verification check and printing a zero-task parallelism summary
with exit 0. The rejection line SHALL name the tail task ids present (or state that the plan
is empty) and instruct the author to add at least one implementation task with `files:`
scope, or retag a tail task whose body is implementation work. The rejection SHALL ride the
existing `PlanShapeError` path so text and `--json` modes exit 1, no compile marker is
written, and the orchestrator refuses to launch. A plan whose fan-out tasks all carry
`status: completed` SHALL NOT be rejected by this rule, since that is a re-compile of a run
already in progress.

#### Scenario: An e2e-only change is rejected
- **WHEN** a `tasks.md` compiles to a single `[e2e]` task and nothing else
- **THEN** `worktrail-compile` exits 1 with a problem line naming that task id and
  instructing the author to add an implementation task, and no run-plan marker is written

#### Scenario: Tail-only plan with several tail kinds is rejected
- **WHEN** a `tasks.md` holds one `[e2e]`, one `[docs]`, and one `[cleanup]` task
- **THEN** `shape_problems` returns a zero-fan-out problem line naming all three ids, in
  addition to any cleanup-verification mismatch line for the `[cleanup]` task

#### Scenario: Re-compile with all fan-out tasks completed still passes
- **WHEN** a plan has two implementation tasks marked `status: completed` and one pending
  `[e2e]` task
- **THEN** `shape_problems` emits no zero-fan-out problem

#### Scenario: A plan with one pending implementation task is unaffected
- **WHEN** a `tasks.md` holds one implementation task with `files:` scope and one `[e2e]` tail
- **THEN** compile emits no zero-fan-out problem and the other plan-shape rules evaluate
  exactly as before this requirement existed

#### Scenario: Compile-gate recovery table names the class
- **WHEN** an operator reads the `#compile-gate` section of the worktrail-go subagent prompts
- **THEN** its defects-in-the-change table has a row for "no fan-out task" whose recovery is
  to add an implementation task or retag the tail task, never a bare retry
