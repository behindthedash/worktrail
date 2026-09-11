## Owning Epic

`docs/specs/epics/001-managed-codex-runtime-validation.md` — Feature 2
(`managed-codex-runtime-attestation`).

## Why

Feature 1's `worktrail-codex-probe` is now available as an on-demand,
credential-safe check of the direct worker preparation and spawn path.  Its
local tests and an operator's local invocation do not establish the boundary
that motivated Epic 001: a managed session can expose an inherited
`CODEX_HOME` that is readable but not writable, and the nested Codex runtime
must still start with the selected provider and usable inherited
authentication.

Maintainers need a repeatable, attributable way to run that safe probe in a
managed session and retain enough redacted evidence to distinguish platform,
provider/authentication, and Worktrail-contract failures.  A successful
process exit alone is insufficient evidence, and raw CLI output or credential
material cannot be used as the missing proof.

## What Changes

- Add an operator-invoked managed-runtime attestation surface which runs the
  existing no-op Codex probe from the installed/current Worktrail path in a
  managed session.  It deliberately supplies a read-only inherited
  `CODEX_HOME`; it does not launch an orchestration task or grant repository
  write access.
- Require the attestation to validate and record only safe evidence: the
  tested Worktrail version and commit identity, a distinct writable child
  home, nested-runtime readiness, the requested and effective Codex provider
  and model identity, usable inherited authentication, the no-op report-back,
  and the probe's classified outcome.
- Persist that evidence as a sanitized attestation entry in the owning
  Worktrail run record.  Failures retain exactly one existing probe stage
  classification and an actionable redacted diagnostic; raw stdout, stderr,
  token values, cookies, and credential-file contents are never persisted.
- Define the one-shot operating procedure and release evidence: run the
  attestation twice from independent fresh managed sessions, plus execute a
  controlled bad-auth or unwritable-child-home case and verify its expected
  classification.  This remains manually invoked and advisory; scheduling,
  retries, alerting, and CI gating belong to Feature 3.

## Capabilities

### New Capabilities

- `managed-codex-runtime-attestation`: a managed-session execution and
  sanitized run-record attestation contract over the existing
  `managed-codex-probe-contract` launcher.

### Modified Capabilities

- `managed-codex-probe-contract`: exposes the non-secret selected/effective
  runtime identity and readiness signals required for a caller to attest the
  direct Codex path without copying raw nested-process output.

## Impact

- `src/worktrail/orchestrator/codex_probe.py` and a focused managed
  attestation CLI/module, with `pyproject.toml` entry-point registration.
- The run-record writer/reader contract and focused orchestrator/router
  tests for serialization, redaction, and failure classification.
- Operator documentation for the two fresh managed-session executions and
  controlled negative case.
- No change to production worker scheduling, `spawnlib.py` behavior, target
  repository contents, default CI, or a recurring canary.
