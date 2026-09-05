## ADDED Requirements

### Requirement: Completed tasks are excluded from plan-shape gating
Before computing the critical-path/width ratio, the same-file dependent-chain length, or the
missing-test-scope check, the compile step SHALL exclude any fan-out task whose `status` is
`"completed"` from the set those three rules evaluate. Dependency edges pointing into an
excluded completed task SHALL be dropped for the purpose of chain computation, the same way
edges into an already-excluded tail-kind task are dropped. A task with no `status` field SHALL
be treated as not completed and remains subject to all three rules.

#### Scenario: A completed predecessor no longer counts toward the serial rule
- **WHEN** a chain of 5 dependent tasks each declaring a distinct file has its first 3 tasks
  marked `status: completed`, leaving a remaining chain of 2 under the default threshold 2
- **THEN** compile emits no serial-rule problem for that chain

#### Scenario: A completed writer no longer extends a same-file chain
- **WHEN** 3 dependent tasks all declare the same file and the first is marked `status:
  completed`, leaving 2 pending tasks under the default same-file threshold 2
- **THEN** compile emits no same-file-chain problem for that file

#### Scenario: A completed task with missing test scope is not flagged
- **WHEN** a task marked `status: completed` declares only a `src/` path with no `tests/`
  path, and a matching test file already exists in the repository
- **THEN** compile emits no missing-test-scope problem for that task

#### Scenario: A fully-pending plan is unaffected
- **WHEN** every fan-out task has `status: pending` or no `status` field at all
- **THEN** the plan-shape gate's three rules evaluate exactly as before this requirement
  existed
