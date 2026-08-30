## Purpose

Defines a no-op probe worker contract and safe launcher that exercises the
same direct Codex environment-preparation and spawn path a real orchestrator
worker uses, so maintainers get deterministic, credential-safe evidence about
that runtime boundary without running a full orchestration job.

## ADDED Requirements

### Requirement: Probe enters the direct orchestrator Codex spawn path
The probe launcher SHALL prepare the Codex child environment and build the
Codex launch command by invoking the same direct-worker preparation and spawn
functions a real orchestrator Codex worker uses, rather than an independent
reimplementation of environment preparation or command construction.

#### Scenario: Probe environment preparation matches the production helper
- **WHEN** the probe launcher prepares its Codex child environment for a run
- **THEN** it does so through the identical environment-preparation entry
  point the production orchestrator Codex worker path calls, with no
  probe-specific fork of that logic

#### Scenario: Probe launch command matches the production helper
- **WHEN** the probe launcher builds the Codex CLI invocation for a run
- **THEN** it does so through the identical command-building entry point the
  production orchestrator Codex worker path calls, with no probe-specific
  fork of that logic

### Requirement: Explicit read-only parent CODEX_HOME is honored, never silently reused
The probe SHALL accept an explicitly read-only parent `CODEX_HOME` as input
and SHALL rely on the existing child-home selection behavior to produce a
writable child home rather than reusing the read-only parent home for the
nested Codex process.

#### Scenario: Read-only parent CODEX_HOME yields a writable child home
- **WHEN** the probe is run with a parent `CODEX_HOME` that exists but denies
  write access
- **THEN** the nested Codex process is launched against a different, writable
  child home, and the read-only parent home is never used as the nested
  process's `CODEX_HOME`

#### Scenario: Child home cannot be made writable
- **WHEN** the probe is run and no writable child home can be resolved or
  created
- **THEN** the probe reports an `environment_preparation` stage failure and
  does not attempt to start the nested Codex process

### Requirement: Probe performs no repository work
The probe's worker contract SHALL be a fixed no-op prompt that instructs the
nested Codex process to perform no file creation, modification, or deletion,
and the probe SHALL verify after each run that no path outside the isolated
child home and the probe's own scratch output changed.

#### Scenario: Successful run leaves the repository unmodified
- **WHEN** a probe run completes any stage outcome (success or failure)
- **THEN** no file under the target repository working tree was created,
  modified, or deleted by the run

#### Scenario: Unexpected mutation is itself a reportable failure
- **WHEN** a probe run detects that a path outside its isolated child home
  and scratch output changed during the run
- **THEN** the probe reports this as a failure rather than treating the run
  as successful

### Requirement: Probe execution is wall-clock bounded
The probe launcher SHALL require a timeout value for every run and SHALL NOT
allow the nested Codex process to run unbounded; a run that exceeds the
timeout SHALL be terminated and reported as a `timeout` stage outcome.

#### Scenario: Probe run exceeds its timeout
- **WHEN** the nested Codex process has not completed by the configured
  timeout
- **THEN** the probe terminates the run and reports a `timeout` stage
  outcome

#### Scenario: No timeout supplied
- **WHEN** the probe launcher is invoked without an explicit timeout
- **THEN** the probe applies a bounded default timeout rather than allowing
  unbounded execution

### Requirement: Sensitive values are redacted from every reported surface
The probe SHALL NOT write raw authentication tokens, cookies, or credential
file contents to stdout, stderr, its structured report, or any persisted
artifact; it SHALL report only presence/usability signals (for example,
"authentication usable: true/false") for anything credential-derived.

#### Scenario: Authentication is usable
- **WHEN** the probe detects that the nested Codex process has usable
  inherited authentication
- **THEN** the structured report records that authentication was usable
  without including any token, cookie, or credential file content

#### Scenario: Authentication is not usable
- **WHEN** the probe detects that the nested Codex process lacks usable
  authentication
- **THEN** the structured report records the `authentication` stage as
  failed without including any token, cookie, or credential file content

#### Scenario: Raw process output contains credential-shaped content
- **WHEN** the nested Codex process's raw stdout or stderr contains
  credential-shaped content (a token, cookie, or file path's contents)
- **THEN** that raw content is excluded from the probe's structured report
  and from any artifact the probe writes

### Requirement: Every run reports exactly one classified stage outcome
The probe SHALL classify every run's outcome as exactly one of:
`environment_preparation`, `startup`, `provider_selection`, `authentication`,
`timeout`, or `report_back`, together with an actionable, redacted
diagnostic message, so a failure can be triaged without re-running the probe
or inspecting raw process output.

#### Scenario: Successful run reports report_back success
- **WHEN** the nested Codex process starts, reports its provider identity,
  demonstrates usable authentication, and returns its no-op reply within the
  timeout
- **THEN** the probe's structured report records a successful `report_back`
  outcome and no earlier-stage failure

#### Scenario: Failure is classified to a single stage
- **WHEN** a probe run fails for any reason
- **THEN** the structured report identifies exactly one of the six stage
  outcomes as the point of failure, with a diagnostic message that does not
  require inspecting raw process output to act on

### Requirement: Probe is independently invocable on demand
The probe SHALL be invocable as a standalone diagnostic (its own entry point)
without depending on an active orchestration run, a target-repository task
plan, or a scheduled/CI trigger.

#### Scenario: Probe runs without an orchestration run in progress
- **WHEN** an operator invokes the probe directly
- **THEN** the probe executes and reports its stage outcome without
  requiring any orchestrator run, task plan, or scheduled trigger to be
  active
