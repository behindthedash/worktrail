## ADDED Requirements

### Requirement: Deterministic same-file ordering repair
When `runplan.apply_to_tasks()` merges a compiled `RunPlan` onto a task list and the
merged result still contains two or more tasks that declare the same file with no
dependency order between them, the system SHALL deterministically insert a `deps` edge
between each such pair — the task that appears later in the tasks' authored order
depends on the task that appears earlier — before returning the merged tasks. The system
SHALL NOT rely solely on the compile-time model prompt to add this edge.

#### Scenario: Model omits the ordering edge for a shared file
- **WHEN** a compiled plan declares the same file on two tasks A and B, A appears before
  B in the tasks' authored order, and neither task's `deps` establishes an ancestor
  relationship between them
- **THEN** `apply_to_tasks()` returns merged tasks in which B's `deps` includes A (directly
  or via an already-present transitive path), and `runplan.unordered_file_collisions()` on
  the returned tasks reports no violation for that file

#### Scenario: Model already ordered the shared file correctly
- **WHEN** a compiled plan declares the same file on two tasks and one is already a
  `deps` ancestor of the other (directly or transitively)
- **THEN** `apply_to_tasks()` makes no additional change to either task's `deps` for that
  file

#### Scenario: Multiple independent collisions in one plan
- **WHEN** a compiled plan leaves more than one file with an unordered same-file pair
- **THEN** `apply_to_tasks()` closes every such gap independently, and the returned merged
  tasks pass `runplan.unordered_file_collisions()` with zero violations

### Requirement: Repair never introduces a cyclic plan
The repair SHALL only add edges consistent with the tasks' fixed authored order (a later
task may depend on an earlier one, never the reverse), and the existing whole-plan cycle
check SHALL run over the repaired dependency graph. If closing collision gaps together
with the plan's own edges produces a cycle, the system SHALL reject the whole plan using
the same fallback behavior already used for other invalid-plan cases: return the original,
unmodified tasks and record why in the returned notes.

#### Scenario: Repair combined with existing edges creates a cycle
- **WHEN** the plan's own edges plus the deterministic same-file repair edges would form a
  cycle in the merged dependency graph
- **THEN** `apply_to_tasks()` returns the original tasks unchanged (not the partially
  repaired merge) and appends a note explaining that the run plan was rejected

### Requirement: Repairs are recorded in run notes
When `apply_to_tasks()` performs at least one same-file ordering repair, it SHALL append a
note to its returned notes list describing that repairs occurred, distinct from the
existing "run plan applied" summary note.

#### Scenario: At least one repair occurred
- **WHEN** `apply_to_tasks()` closes one or more same-file ordering gaps
- **THEN** the returned notes list includes an entry indicating that automatic ordering
  repair occurred

#### Scenario: No repair was needed
- **WHEN** the merged tasks already satisfy `runplan.unordered_file_collisions()` with no
  violations before any repair logic runs
- **THEN** the returned notes list contains no repair-specific entry
