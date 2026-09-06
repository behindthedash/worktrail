## ADDED Requirements

### Requirement: Every spawn call site handles or declares exemption from exhaustion
Every call site of the shared agent-spawn helpers in the package source SHALL either act on the
exhaustion signal -- by branching on it, or by raising the shared capacity-exhaustion exception
at the boundary -- or be recorded in an enforcement test's allowlist together with a written
rationale for why that site's existing behaviour is already correct. An automated test SHALL
fail the build when a call site does neither, so a newly added caller cannot silently read a
given-up spawn's output as an answer.

The shared capacity-exhaustion exception SHALL be a subclass of the existing
"no execution target could serve this" error, so that a caller which already handles the latter
handles an exhausted spawn correctly without change, and SHALL carry the failure class and a
description of the call site it was raised from.

#### Scenario: A new unchecked caller fails the build
- **WHEN** a call to an agent-spawn helper is added in the package source whose result is neither
  checked for exhaustion nor passed to the shared raise helper, and the site is not allowlisted
- **THEN** the enforcement test fails, naming the file and line of the offending call

#### Scenario: An exempt caller is recorded with a reason
- **WHEN** a call site is exempt because it never converts the spawn's output text into a result
  -- reading only the session id, or reporting the unexpected output as its own outcome
- **THEN** it appears in the allowlist with the rationale, and the enforcement test passes

#### Scenario: The capacity exception is recognised by existing handlers
- **WHEN** a caller catches the "no execution target" error and an exhausted spawn raises
- **THEN** that existing handler catches it, and the raised error carries the failure class and
  the originating call-site description

### Requirement: A capacity-blocked compile is reported as capacity, not as a bad answer
When the run-plan compile worker's spawn is exhausted, the compile SHALL NOT interpret the
provider's output as a model response: no payload SHALL be extracted from it, and the degrade
note recorded on the resulting baseline plan SHALL name the capacity block rather than
attributing the degrade to a missing or rejected model answer. The degraded plan SHALL NOT be
cached, so a later attempt gets a fresh compile once capacity returns.

The compile command SHALL surface the block distinguishably: it SHALL print a
`blocked_no_capacity:` diagnostic on stderr and exit with status 2. Exit status 1 SHALL retain
its existing meaning -- the plan was produced but has scope gaps, unordered file collisions, or
uncovered requirements -- conditions a re-run alone will not resolve.

#### Scenario: The compile worker never answers
- **WHEN** the compile spawn returns exhausted with the provider's usage-limit text
- **THEN** the run falls back to the artifact's own declared dependencies with a note naming the
  capacity block, and no plan derived from that text is stored in the plan cache

#### Scenario: The command exit distinguishes capacity from a bad plan
- **WHEN** the compile command's spawn is exhausted
- **THEN** stderr carries a `blocked_no_capacity:` line and the exit status is 2, distinct from
  the status 1 used for scope gaps, collisions, and uncovered requirements

### Requirement: A capacity block costs no group-worker strike
When a group-level resolve or ci-fix worker's spawn is exhausted, the verify loop SHALL NOT
treat it as a worker failure: the spawn's output SHALL NOT be parsed as a report-back, the
group's strike budget SHALL NOT be decremented, and the group SHALL NOT be quarantined with a
reason attributing the failure to a worker. The loop SHALL log the capacity block, stop
attempting further workers for that group in this run, and leave the group's branch and pull
request unchanged for a later run.

#### Scenario: Every resolve worker is capacity gated
- **WHEN** the resolve loop's first worker spawn returns exhausted
- **THEN** no further resolve worker is spawned for that group in this run, the strike count is
  unchanged, and the group is reported as capacity blocked rather than quarantined

#### Scenario: The error stream is not read as a report-back
- **WHEN** a ci-fix worker's spawn returns exhausted with the provider's error text
- **THEN** that text is not parsed for a report-back status and does not produce a
  report-back-parse-failure strike

### Requirement: A capacity-blocked task worker raises instead of reporting back
When a task worker's spawn is exhausted, the live spawn wrapper SHALL raise the shared
capacity-exhaustion exception rather than returning the provider's output to the drive loop, so
that the output is never parsed as a report-back and the task's implement/fix attempt budget is
not consumed by an attempt no model served. The served-harness label correction that a spawn
already performs SHALL still apply before the raise, so the run journal records which cell was
attempted.

#### Scenario: A worker spawn gives up on capacity
- **WHEN** a task worker's spawn returns exhausted
- **THEN** the wrapper raises the capacity-exhaustion exception, the drive loop's report-back
  parser is never called with the provider's text, and the task's attempt budget is unchanged

#### Scenario: A successful worker spawn is unaffected
- **WHEN** a task worker's spawn returns a normal, non-exhausted result
- **THEN** the wrapper returns it unchanged, including its served-harness label correction
