## 1. Managed attestation implementation

- [x] 1.1 Add the managed Codex runtime-attestation module and
      `worktrail-*` entry point.  It must create the deliberate read-only
      parent-home fixture, invoke the existing probe rather than rebuilding
      its spawn path, require a timeout and owning run-record path, verify the
      distinct writable child home, and return a non-zero status after
      recording every failed result.  Extend the probe's safe result only as
      necessary to provide equality-checkable selected/effective provider and
      model identity; missing or mismatched effective identity must classify
      as `provider_selection`, never as a session/thread-id substitute.
      (Requirements: Attestation runs the safe probe under the managed
      boundary; Success attests all direct-runtime signals.)
      Add a narrow, versioned managed-attestation entry API to `run_record`
      and call it from the command.  Validate the allowlisted identity,
      source-version/commit, nonce, boolean signals, stage, success, and
      diagnostic fields; reject raw output, credential-shaped values,
      credential-file locations, arbitrary environments, and invalid stages.
      Write exactly one entry for either a successful or failed invocation
      through the run-record writer, never by hand-editing YAML.
      (Requirement: Attestation evidence is sanitized and attributable.)
      In `tests/orchestrator/test_codex_probe.py`, a new focused attestation
      test module, and `tests/router/test_run_record.py`, cover direct probe
      reuse, read-only-parent to writable-distinct-child validation, timeout
      propagation, readiness, identity match/mismatch/missing behavior,
      authentication rejection, unchanged no-op repository scope, valid
      success/failure serialization, one-entry failure persistence before
      non-zero exit, field/stage validation, and rejection of each unsafe
      payload class.
  files: src/worktrail/orchestrator/codex_probe.py, src/worktrail/orchestrator/codex_runtime_attestation.py, src/worktrail/router/run_record.py, pyproject.toml, tests/orchestrator/test_codex_probe.py, tests/orchestrator/test_codex_runtime_attestation.py, tests/router/test_run_record.py

## 2. Managed-session operating procedure

- [x] 2.1 Document the operator invocation and evidence review procedure:
      invoke the installed/current Worktrail command with a run-record path;
      perform two independent fresh managed-session passes with distinct
      nonces and matching intended source identity; then perform a controlled
      bad-auth or unwritable-child-home run and verify its classified,
      sanitized entry.  State explicitly that this is advisory and creates no
      schedule, retry loop, alert, CI requirement, or release gate.
      (Requirement: Fresh managed-session evidence is repeatable and
      advisory.)
  files: docs/specs/epics/001-managed-codex-runtime-validation.md

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator/test_codex_probe.py tests/orchestrator/test_codex_runtime_attestation.py tests/router/test_run_record.py`.
- [ ] 3.2 [e2e] In the managed platform, run the documented attestation twice
      from fresh sessions and once with the controlled negative condition;
      inspect the owning run record after each invocation to confirm only
      sanitized evidence was written.
- [ ] 3.3 [e2e] Run `openspec validate managed-codex-runtime-attestation --strict`
      and `worktrail-compile openspec/changes/managed-codex-runtime-attestation`.
