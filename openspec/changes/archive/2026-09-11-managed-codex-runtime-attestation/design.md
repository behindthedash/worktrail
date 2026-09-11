## Context

`managed-codex-probe-contract` provides `worktrail-codex-probe`, a bounded
no-op diagnostic that follows the direct Codex child-environment and command
construction path.  It deliberately runs in a scratch directory and emits a
redaction-safe `ProbeReport`.  It is intentionally only a local/on-demand
probe: it neither establishes that the managed platform presents the
read-only-parent-home condition nor records release evidence in a Worktrail
run record.

Feature 2 supplies that operational boundary.  The meaningful proof is not a
successful process spawn: a managed attestation must prove child-home
isolation, runtime readiness, selection preservation, usable authentication,
and no-op report-back together, while leaving no credential-derived data in
the run record.

## Goals / Non-Goals

**Goals:**

- Run the existing safe probe explicitly from the installed/current
  Worktrail path in a managed session with a deliberately non-writable parent
  `CODEX_HOME`.
- Produce a small, schema-validated, redaction-safe attestation record tied
  to a specific Worktrail run record and tested source identity.
- Preserve the probe's single-stage failure taxonomy and make every failure
  actionable without retaining raw nested-process output.
- Give operators a repeatable two-fresh-session release-evidence procedure,
  including one controlled negative case.

**Non-Goals:**

- Reimplementing `prepare_codex_child_environment`, command construction,
  timeout handling, or no-op mutation checking from Feature 1.
- Turning the probe into a task worker, changing normal worker scheduling, or
  running it against a target repository.
- Scheduling, retrying, alerting on, or making the attestation a CI/release
  gate.  Those are the deferred Feature 3 canary decision.
- Persisting a credential, credential-file path or contents, raw stdout or
  stderr, account identity, or a full managed-environment dump.

## Decisions

### 1. Wrap the probe; do not create a second launcher

The attestation command calls the Feature 1 probe API/entry point and uses its
classified `ProbeReport` as its only nested-runtime result.  It prepares a
test-only read-only parent home in managed-session-owned scratch space and
passes that home explicitly.  The wrapper must verify that the reported child
home is distinct and writable before accepting a success.  This retains the
production-path parity and no-repository-work guarantees already owned by the
probe instead of duplicating fragile subprocess logic in an operational
script.

### 2. Identity is a safe, equality-checked launch/result pair

The attestation captures the explicitly selected Codex provider and model
(where a model is configured) before launch, and the corresponding normalized
effective identity reported by the direct Codex path.  Success requires the
provider identities to agree, and requires configured model identities to
agree.  The implementation may use only documented, non-secret runtime
signals; it must fail as `provider_selection` when the effective identity is
absent or disagrees rather than substituting a session/thread id as identity.

The fixed direct-Codex provider label is still recorded explicitly.  This
matters because "the command started" is not evidence that the requested
provider lane or model was retained.

### 3. Persist one namespaced, validated, sanitized attestation entry

The command receives an owning run-record path and writes a single structured
attestation entry through `run_record`'s writer API, never by editing YAML.
Its stable fields are: schema version; timestamp; Worktrail version and git
commit (or an explicit unavailable value); managed-session nonce; selected
and effective provider/model identity; child-home-isolated/writable boolean;
runtime-ready boolean; authentication-usable boolean; report-back-success
boolean; final stage; success; and sanitized diagnostic.

The nonce distinguishes fresh executions without identifying an operator or
session.  The writer validates the allowed fields and stage enum, rejects
credential-shaped values, and never accepts raw stdout/stderr or arbitrary
environment mappings.  A failed attestation writes the same shape with the
single classified stage; it must not be omitted merely because the command
exits non-zero.

### 4. Fresh-session proof is an operational acceptance criterion

The implementation supplies a documented command sequence, but does not
automate a schedule.  Before claiming release evidence, an operator runs it
twice from separate fresh managed sessions against the same intended
Worktrail identity and records two successful, distinct-nonce entries.  The
operator also runs one controlled bad-auth or unwritable-child-home case and
confirms that it is recorded as `authentication` or
`environment_preparation`, respectively.  This differentiates a platform
boundary failure from a nominal local probe result without hiding failure
behind retries.

## Risks / Trade-offs

- A managed platform may not permit construction of a read-only fixture home.
  That is an `environment_preparation` attestation failure with a safe
  diagnostic, not a reason to silently use its ambient writable home.
- Effective provider/model identity may not be available from a documented
  nested-runtime signal.  Failing `provider_selection` is preferable to the
  current false confidence of treating a session marker as identity.
- Run records are long-lived operational artifacts.  A narrow allowlist and
  redaction tests make an evidence extension deliberate rather than allowing
  an arbitrary diagnostics blob to become persistent telemetry.

## Migration Plan

No data migration is required.  The new command is opt-in and creates an
attestation entry only for its supplied run record.  Rollback removes the
wrapper and entry schema; historical sanitized entries remain valid audit
artifacts.  Feature 1's standalone diagnostic and the normal worker path are
unaffected.
