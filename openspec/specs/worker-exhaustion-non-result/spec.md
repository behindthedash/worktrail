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
