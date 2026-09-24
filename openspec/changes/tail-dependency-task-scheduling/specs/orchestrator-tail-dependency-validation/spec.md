## Purpose

Prevents a live orchestrator run from accepting dependency graphs that defer
required implementation work into a scheduler pass that cannot execute it.

## ADDED Requirements

### Requirement: Tail dependency inversions are rejected before scheduling
The system SHALL reject a pending non-tail task that directly depends on an
`e2e` or `cleanup` tail task before starting a live orchestrator run. The
diagnostic SHALL identify the non-tail task and the tail dependency that make
the graph unschedulable. Dependencies from a tail task to another tail task,
and dependencies between non-tail tasks, SHALL remain valid.

#### Scenario: Implementation task depends on an e2e task
- **WHEN** a pending implementation task declares an `e2e` task in its dependencies
- **THEN** run-plan compilation fails and names both task ids in its diagnostic

#### Scenario: Cleanup task depends on an e2e task
- **WHEN** a pending `cleanup` task declares an `e2e` task in its dependencies
- **THEN** run-plan compilation accepts that dependency

#### Scenario: Live precheck encounters an inversion
- **WHEN** precheck evaluates a change containing a pending non-tail task that depends on a tail task
- **THEN** it prints the plan-shape diagnostic and returns a non-zero exit status

#### Scenario: Completed implementation task has a historical tail dependency
- **WHEN** an already completed non-tail task declares a dependency on a tail task
- **THEN** validation does not reject the plan solely for that historical dependency
