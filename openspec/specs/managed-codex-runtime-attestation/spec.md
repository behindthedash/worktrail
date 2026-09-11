# managed-codex-runtime-attestation Specification

## Purpose
Defines the managed-session attestation that turns the safe direct-Codex probe
into sanitized, attributable release evidence without permitting repository
work or credential disclosure.
## Requirements
### Requirement: Attestation runs the safe probe under the managed boundary

The attestation SHALL invoke the `managed-codex-probe-contract` launcher from
the installed/current Worktrail path in a managed session, with an explicitly
read-only inherited `CODEX_HOME`.  It SHALL NOT create an orchestration task
or use a target repository as the nested process working directory.

#### Scenario: Read-only managed parent home is isolated

- **WHEN** an operator invokes the attestation in a managed session with its
  required timeout and run-record path
- **THEN** it supplies a read-only parent `CODEX_HOME`, accepts success only
  if the probe reports a different writable child home, and preserves the
  probe's no-op scope checks

#### Scenario: Child home preparation cannot meet the boundary

- **WHEN** the managed session cannot create or resolve the required writable
  child home
- **THEN** the attestation records and returns an
  `environment_preparation` failure and does not treat the ambient parent
  home as an acceptable fallback

### Requirement: Success attests all direct-runtime signals

An attestation SHALL be successful only when the child home is isolated and
writable, the nested Codex runtime reports readiness, the effective provider
and model identity (when the runtime exposes it) agree with the selected
identity, inherited authentication is usable, and the fixed no-op report-back
succeeds within the configured timeout. An effective identity that the
runtime does not expose is recorded as unverified and does not by itself fail
the attestation — codex-cli 0.154.0 (the current stable release) does not
report provider/model identity on `thread.started` at all, so "not reported"
cannot be distinguished from "correct but unobservable" (see
`docs/specs/epics/001-managed-codex-runtime-validation.md` Feature 2). A
reported identity that disagrees with the selected one is always a failure:
that is real evidence of a problem, not a tooling gap.

#### Scenario: Managed runtime satisfies every signal

- **WHEN** the managed child starts, reports the selected effective identity,
  accepts inherited authentication, and returns the expected no-op reply
- **THEN** the attestation records success with readiness, identity,
  authentication, and report-back signals all true

#### Scenario: Runtime identity is unverifiable

- **WHEN** the nested runtime does not expose a documented non-secret
  effective provider/model identity, but every other signal (readiness,
  authentication, report-back) passes
- **THEN** the attestation records success, and the run-record entry records
  the effective identity fields as unverified (null) rather than failing at
  `provider_selection`

#### Scenario: Runtime identity disagrees with the selected one

- **WHEN** the nested runtime reports an effective provider or model identity
  that differs from the selected one
- **THEN** the attestation fails at `provider_selection` rather than using a
  session/thread marker as an identity substitute

#### Scenario: Authentication is rejected after startup

- **WHEN** the nested runtime reaches the provider but rejects inherited
  authentication
- **THEN** the attestation records an `authentication` failure with a
  normalized diagnostic and does not reclassify it as startup failure

### Requirement: Attestation evidence is sanitized and attributable

For every attempted attestation, the command SHALL write one validated,
sanitized entry through the owning Worktrail run record.  The entry SHALL
identify the tested Worktrail version and commit when available, a
non-identifying fresh-session nonce, selected/effective identity, safe boolean
signals, one classified stage, success, and a redacted diagnostic.  It SHALL
NOT contain raw subprocess output, credentials, cookies, credential-file
contents or paths, account identity, or an unrestricted environment dump.

#### Scenario: Successful evidence is persisted

- **WHEN** an attestation succeeds
- **THEN** its owning run record contains exactly one schema-valid sanitized
  success entry associated with that invocation

#### Scenario: Failed evidence is persisted

- **WHEN** an attestation fails at any probe stage
- **THEN** its owning run record contains exactly one sanitized failure entry
  with that single stage and an actionable redacted diagnostic before the
  command exits non-zero

#### Scenario: Unsafe evidence is offered for persistence

- **WHEN** an attestation payload includes raw process output, a
  credential-shaped value, an arbitrary environment mapping, or a
  credential-file location
- **THEN** the run-record writer rejects it and no unsafe value is written

### Requirement: Fresh managed-session evidence is repeatable and advisory

The attestation surface SHALL document an operator procedure requiring two
successful executions from independent fresh managed sessions and one
controlled bad-auth or unwritable-child-home execution before the result is
used as release evidence.  The procedure SHALL remain manually invoked and
advisory.

#### Scenario: Two fresh managed sessions pass

- **WHEN** an operator completes the documented procedure in two separate
  fresh managed sessions
- **THEN** the run record contains two successful entries with distinct
  nonces and the same intended Worktrail identity

#### Scenario: Controlled negative case is exercised

- **WHEN** an operator runs the documented bad-auth or unwritable-child-home
  case
- **THEN** the resulting entry is classified as `authentication` or
  `environment_preparation` respectively and is not reported as a successful
  attestation

#### Scenario: No canary policy is configured

- **WHEN** this change is implemented without an explicit later canary
  decision
- **THEN** no schedule, retry loop, alert, CI requirement, or release gate is
  enabled by the attestation surface

