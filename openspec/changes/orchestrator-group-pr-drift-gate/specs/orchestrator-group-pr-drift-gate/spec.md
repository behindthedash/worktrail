## Purpose

Ensures every pull request the orchestrator opens for a delivery group clears the same
deterministic spec/task drift checks a one-off route must clear, evaluated against that
group's own integrated tree, so a group PR cannot ship drift — most importantly a
mechanically-marked `completed` task whose Definition of Done does not actually hold.

## ADDED Requirements

### Requirement: A checks-only gate mode runs the drift checks without the test command

The pre-PR gate SHALL offer a mode that evaluates exactly the four deterministic drift
checks — spec-sync, clarification-integrity, Definition-of-Done verification, and
requirement/AC-to-task coverage — against the tree named by its repo argument, and SHALL
NOT execute the repo policy's configured pre-PR test command in that mode. The mode SHALL
report each failure class with the same distinct exit code the default gate path already
uses for it, and SHALL exit successfully when no drift is found.

This mode SHALL NOT evaluate scope-completeness review, which depends on a run record the
caller of this mode does not have.

#### Scenario: Drift-free tree in checks-only mode

- **WHEN** the gate is invoked in checks-only mode against a tree with no spec-sync,
  clarification-integrity, DoD, or requirement-coverage drift
- **THEN** it exits 0, and the repo policy's pre-PR test command is not executed

#### Scenario: Each drift class keeps its own exit code

- **WHEN** the gate is invoked in checks-only mode against a tree carrying exactly one
  class of drift
- **THEN** it exits with that class's established code — spec-sync, clarification
  integrity, DoD verification, and requirement/AC coverage each distinct — and reports the
  offending items on standard error

#### Scenario: The test command is never run in checks-only mode

- **WHEN** the gate is invoked in checks-only mode against a repo whose policy configures
  a pre-PR test command that would fail if run
- **THEN** the gate's result is determined solely by the drift checks, and the configured
  command is not executed

#### Scenario: Scope-completeness review is not evaluated

- **WHEN** the gate is invoked in checks-only mode and no run record is supplied
- **THEN** the absence of a scope review does not cause a failure

#### Scenario: The existing label-printing mode is unaffected

- **WHEN** the gate is invoked in its label-printing mode
- **THEN** it behaves exactly as before this change — printing the resolved labels and
  exiting 0 without running any drift check or the test command

### Requirement: Every orchestrator group PR clears the drift gate before it exists

Before pushing a group branch or opening its pull request, the orchestrator SHALL run the
checks-only gate for that group and SHALL treat a non-zero result as a blocking failure.
This applies to every delivery group in the run, not only the group that carries the spec
folder, because the Definition-of-Done population the orchestrator writes spans all
groups.

The gate SHALL run after the orchestrator has written its `completed` task statuses onto
the group branch, so Definition-of-Done verification evaluates the exact task population
the orchestrator just claimed complete. It SHALL run before the group's integration smoke
command, so a drift failure costs no test run.

#### Scenario: Clean group opens its PR unchanged

- **WHEN** a group integrates cleanly and the drift gate passes
- **THEN** integration continues exactly as before — the smoke command runs if configured,
  the branch is pushed, and the pull request is created with its resolved labels

#### Scenario: Gate runs on a group that carries no spec folder

- **WHEN** a non-spec-carrier group reaches the gate, having had its spec folder reset to
  the base
- **THEN** the gate still runs for that group, and the spec-scoped checks find nothing to
  fail on while Definition-of-Done verification still evaluates that group's own task
  files

#### Scenario: Gate precedes the smoke command

- **WHEN** a group's drift gate fails and an integration smoke command is configured
- **THEN** the smoke command is not run for that group

### Requirement: The gate inspects the group's integrated tree, not the canonical checkout

The orchestrator SHALL point the checks-only gate at the group's own integration worktree.
Because the gate derives its changed-file set by diffing that tree's `HEAD` against the
resolved base ref, pointing it at the canonical checkout — whose `HEAD` is the base branch
— would produce an empty diff and make every diff-scoped check pass vacuously.

#### Scenario: Gate is invoked against the integration worktree

- **WHEN** the orchestrator invokes the checks-only gate for a group
- **THEN** the repo argument it passes is that group's integration worktree path, the same
  path used to write the group's task statuses and to run its smoke command, and not the
  canonical repository checkout

#### Scenario: Drift present only on the group branch is detected

- **WHEN** a group branch introduces drift that does not exist on the base branch
- **THEN** the gate detects it, because the diff it evaluates is the group branch's own
  changes against the base

### Requirement: A drift failure quarantines the group instead of opening a PR

When the checks-only gate returns non-zero for a group, the orchestrator SHALL quarantine
that group: record a quarantine reason carrying a short excerpt of the gate's failure
output, report the skip on its progress output, journal the group as quarantined with a
reason code that identifies pre-PR drift specifically and distinguishes it from a generic
integration error, and abandon that group's integration. No branch push and no pull
request SHALL occur for a quarantined group.

A gate invocation that cannot complete — the gate is unresolvable, times out, or fails to
spawn — SHALL be treated as a failure, never as a pass.

#### Scenario: Drift failure blocks the PR

- **WHEN** the drift gate returns non-zero for a group
- **THEN** the group is quarantined, no pull request is created for it, and the run
  continues with the remaining groups

#### Scenario: Quarantine record identifies the cause

- **WHEN** a group is quarantined by the drift gate
- **THEN** its journal entry carries a quarantine reason code distinct from the codes used
  for budget exhaustion, task failure, merge conflict, generic integration error, and
  dependency quarantine, and its human-readable reason includes an excerpt of the gate's
  failure output

#### Scenario: Gate cannot be run

- **WHEN** the gate cannot be resolved, times out, or fails to spawn
- **THEN** the group is quarantined rather than allowed through
