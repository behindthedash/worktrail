# worker-exhaustion-non-result Specification

## Purpose
TBD - created by openspec sync for change triage-evaluator-capacity-non-verdict. Update
Purpose after archive.

## Requirements
### Requirement: A spawn that gave up is signalled as exhausted
When the shared agent-spawn helper stops attempting a spawn without a model answer -- its
session-limit wait budget is spent, its retry budget is spent with no alternate cell left in
the routing row, or its retry loop falls out -- the `SpawnResult` it returns SHALL carry an
`exhausted` flag set true and the `failure_class` already computed for that failure by the
capacity classifier (for example `billing` for a provider usage cap). A spawn that completes
without an infra failure SHALL return `exhausted` false and SHALL be unchanged in every other
respect. Both fields SHALL default to "not exhausted" / "no failure class", so a caller that
does not read them behaves exactly as before.

#### Scenario: Usage cap with no alternate cell
- **WHEN** every cell in the row has spent its retry budget against a provider usage-limit
  failure and the helper gives up
- **THEN** the returned result carries `exhausted` true and the `billing` failure class

#### Scenario: Session-limit wait budget spent
- **WHEN** the helper exhausts its session-limit wait budget with no alternate cell to hop to
- **THEN** the returned result carries `exhausted` true

#### Scenario: A successful spawn is unmarked
- **WHEN** a spawn returns without an infra failure
- **THEN** the returned result carries `exhausted` false and an empty failure class, and its
  text, usage, session id, and served cell fields are unchanged

### Requirement: An exhausted spawn's output is never used as a result value
No consumer of the agent-spawn helper SHALL convert an exhausted spawn's output text into a
stored or reported result value -- a triage verdict, a triage note, a brief filename slug, or
any other durable record. A consumer SHALL either fail closed on the exhaustion signal or fall
back to a deterministic non-model path that ignores the output text entirely.

#### Scenario: Handoff capture falls back to its deterministic slug
- **WHEN** the summariser spawn that proposes a concise slug for a new handoff brief returns
  exhausted
- **THEN** capture discards its output text and names the brief with the deterministic
  focus-derived slug, exactly as it does when no summariser backend is configured

#### Scenario: The exhausted output is not stored anywhere
- **WHEN** any consumer receives an exhausted spawn result
- **THEN** the provider's error text appears in no brief body, no brief filename, no verdict
  file, and no triage note

### Requirement: An exhausted evaluator spawn yields no verdict
When the triage evaluator's spawn for a brief group returns exhausted, the evaluation of that
group SHALL fail closed: the group's raw output SHALL NOT be parsed for verdicts, no verdict
(including the fail-open `keep`) SHALL be produced for any brief in the group, and the failure
SHALL be raised to the caller as a distinct evaluator-unavailable outcome naming the group's
repo, its brief ids, and the failure class. The briefs in that group SHALL be left byte-for-byte
unchanged -- no frontmatter edit, no appended triage note, no `keep-count` increment -- and
SHALL remain queued for a later evaluation run.

#### Scenario: Every worker fails on capacity
- **WHEN** a group's evaluator spawn returns exhausted with a provider usage-cap failure class
- **THEN** no verdict is recorded for any brief in that group, and each brief's file content is
  identical to what it was before the run

#### Scenario: The error stream is not treated as a malformed verdict
- **WHEN** an exhausted evaluator spawn's output text is the provider's usage-limit message
- **THEN** that text is not parsed, not retained as evidence, and does not become a `keep`
  verdict

#### Scenario: The keep-escalation counter is not advanced
- **WHEN** a brief with an existing `keep` streak is in a group whose evaluator spawn is
  exhausted
- **THEN** the brief's trailing `keep` streak is unchanged, so its escalation threshold is not
  reached by a run in which no model read it

### Requirement: Capacity exhaustion exits non-zero and distinguishably
The single-brief evaluate entrypoint SHALL, on an evaluator-unavailable outcome, print a null
verdict on stdout, print a `blocked_no_capacity:` diagnostic naming the failure on stderr, and
exit with status 2 -- the same status and diagnostic shape the dispatch entrypoint already uses
when no execution target has capacity. Exit status 1 SHALL retain its existing, narrower
meaning: the evaluator ran and produced no identifiable verdict for the requested brief id.

The batch evaluate command SHALL NOT discard the verdicts of groups that did evaluate
successfully: it SHALL omit each unavailable group's briefs from the verdict file, report the
number of unevaluated groups in both its JSON and its text summary, and exit non-zero whenever
that number is greater than zero.

#### Scenario: Single-brief evaluation is capacity blocked
- **WHEN** `--evaluate-brief-triage` is run for a brief whose evaluator spawn is exhausted
- **THEN** stdout is `null`, stderr carries a `blocked_no_capacity:` line, and the exit status
  is 2

#### Scenario: No identifiable verdict still exits 1
- **WHEN** the evaluator runs to completion but emits nothing identifiable for the requested
  brief id
- **THEN** stdout is `null` and the exit status is 1, unchanged by this change

#### Scenario: One gated group does not lose another group's work
- **WHEN** a batch evaluate run has two groups and only the second one's spawn is exhausted
- **THEN** the verdict file contains the first group's verdicts, contains none of the second
  group's briefs, the summary reports one unevaluated group, and the command exits non-zero

### Requirement: The apply path refuses a verdictless payload
The apply entrypoint SHALL reject a payload that is null, is not a verdict object, or carries a
null or empty `verdict` field: it SHALL return an `error` action-log entry naming the reason,
exit non-zero, and SHALL NOT append a triage note, increment a `keep-count`, edit a brief, open
a pull request, or close a brief. It SHALL NOT raise an unhandled exception for such a payload.

#### Scenario: The evaluate step's null output is piped into apply
- **WHEN** a caller passes the null output of a capacity-blocked evaluate step to the apply
  entrypoint
- **THEN** an `error` action-log entry is returned, the exit status is non-zero, and the brief
  is unmodified

#### Scenario: A verdict object with no verdict type
- **WHEN** the apply payload is an object whose `verdict` field is null or empty
- **THEN** the same `error` entry and non-zero exit result, with no brief written

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
