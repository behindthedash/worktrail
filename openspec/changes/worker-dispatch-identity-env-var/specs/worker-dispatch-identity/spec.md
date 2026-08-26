## Purpose
Gives every headless worker process the orchestrator spawns access to its run's own
dispatch identity, so a tool-use hook running inside that worker's session can prove
its edits belong to the run that owns the worktree rather than an external actor.

## ADDED Requirements

### Requirement: Worker environment carries the run's dispatch identity
When the orchestrator's worker-spawn path is given a dispatch identity for the
current run, it SHALL export that identity into the environment of every headless
worker process it launches, under a stable, documented environment variable name
(`WORKTRAIL_DISPATCH_ID`), so any subprocess a worker's own session spawns (including
a tool-use hook) inherits it.

#### Scenario: Dispatch identity supplied
- **WHEN** the orchestrator spawns a headless worker and a dispatch identity was
  supplied for the run
- **THEN** the spawned worker's process environment contains `WORKTRAIL_DISPATCH_ID`
  set to exactly that identity

#### Scenario: Multiple workers in one run share the same identity
- **WHEN** the orchestrator spawns more than one headless worker within the same run,
  with the same dispatch identity supplied for that run
- **THEN** every one of those workers' process environments contains the same
  `WORKTRAIL_DISPATCH_ID` value

### Requirement: No dispatch identity is invented when none is supplied
When the orchestrator's worker-spawn path is not given a dispatch identity for the
current run, it SHALL NOT set `WORKTRAIL_DISPATCH_ID` in a spawned worker's
environment, and SHALL NOT fabricate a substitute identity.

#### Scenario: No dispatch identity supplied
- **WHEN** the orchestrator spawns a headless worker and no dispatch identity was
  supplied for the run
- **THEN** the spawned worker's process environment does not contain
  `WORKTRAIL_DISPATCH_ID`
