# Epic 001: Managed Codex Runtime Validation

**Status:** Proposed  
**Owner:** Worktrail maintainers  
**Origin:** Handoff `20260812-123852-add-a-managed-environment-end`  
**Prerequisite:** PR #323 (`fix: isolate Codex orchestrator worker homes`), merged as `b6dc96e`

## Business objective

Give Worktrail maintainers and operators direct evidence that a nested Codex worker can start through the production orchestrator path when the managed platform exposes a read-only inherited `CODEX_HOME`. This closes the gap between PR #323's stubbed process tests and the boundary that failed in the managed environment, so releases do not depend on an unverified runtime assumption.

The smallest complete outcome is one credential-safe live probe that starts the nested Codex app server, preserves the selected provider and usable authentication, performs no repository work, and leaves sanitized run-record evidence.

## Personas

- **Worktrail maintainer:** needs release evidence that worker environment preparation functions outside local test doubles.
- **Managed-environment operator:** needs an actionable distinction between child-home preparation, provider selection, authentication, and report-back failures.
- **Security reviewer:** needs proof that the probe neither logs credentials nor grants the nested worker unnecessary repository access.

## Scope

- Exercise the direct orchestrator Codex worker path that uses `src/worktrail/orchestrator/spawnlib.py`.
- Deliberately provide a readable but non-writable inherited `CODEX_HOME` and verify that Worktrail prepares a writable child home.
- Verify nested app-server startup, provider identity, authentication usability, and a bounded report-back.
- Prevent repository work by using a no-op probe contract and the minimum filesystem roots needed for startup.
- Emit sanitized, attributable evidence into the owning Worktrail run record.
- Define a repeatable managed-runtime regression signal after the one-shot proof is understood.

## Non-goals

- Re-testing every parallel-orchestrator task or launching implementation work in a target repository.
- Printing, copying into artifacts, or otherwise exposing authentication tokens, cookies, or credential files.
- Replacing unit and integration coverage around `spawnlib.py` or `skill_dispatch.py`.
- Broadening nested-worker permissions to make the probe pass.
- Treating a successful process spawn without provider, authentication, and report-back evidence as success.
- Making the managed probe a required CI gate before its reliability and operating cost are measured.

## Success metrics

- A managed run with a deliberately read-only inherited `CODEX_HOME` reaches nested Codex app-server readiness through the direct orchestrator path.
- The child reports the intended provider identity and demonstrates usable inherited authentication without credential material appearing in stdout, stderr, run records, or committed artifacts.
- The probe completes a bounded no-op/report-back contract and causes no target-repository mutation.
- Every failure is classified at least as environment preparation, startup, provider selection, authentication, timeout, or report-back, with an actionable sanitized diagnostic.
- A second execution from a fresh managed session reproduces the same result before any recurring gate is enabled.

## Feature decomposition

### Feature 1 — Managed probe contract and safe launcher

**Future spec id:** `managed-codex-probe-contract`

Define the smallest no-op worker contract and launcher that enters the same direct Codex preparation/spawn path as a real orchestrator worker while prohibiting repository work. The probe accepts an explicitly read-only parent home, bounds execution time, redacts sensitive values, and reports structured stage outcomes.

**Independent value:** maintainers gain a deterministic local or managed diagnostic for the exact runtime boundary without running an orchestration job.

**Release evidence:** tests prove path parity with the production spawn helper, no-op scope enforcement, timeout behavior, and secret redaction.

### Feature 2 — Managed-environment startup and identity attestation

**Future spec id:** `managed-codex-runtime-attestation`

Run the safe launcher in the managed environment against the published/current Worktrail path. Attest that the child home is writable and isolated, the nested app server becomes ready, the selected provider is preserved, inherited authentication is usable, and the bounded report-back succeeds. Store only sanitized stage results and relevant version/commit identity in the run record.

**Independent value:** operators receive the missing end-to-end proof for PR #323 and can distinguish a platform boundary regression from an authentication or provider problem.

**Release evidence:** two fresh managed sessions pass with no credential disclosure and no repository mutation; a controlled bad-auth or non-writable-child-home case produces the expected classified failure.

### Feature 3 — Bounded recurring regression signal

**Future spec id:** `managed-codex-runtime-canary`

Package the attestation as an opt-in scheduled or release-time canary with explicit cost, retry, retention, and escalation boundaries. Keep it advisory until observed reliability is sufficient; never silently downgrade failure to success.

**Independent value:** maintainers learn about managed-platform or nested-runtime drift before a real orchestration run is lost.

**Release evidence:** documented invocation and ownership, bounded retries/timeouts, sanitized evidence retention, and distinct alerts for platform, provider/authentication, and Worktrail contract failures.

## Dependencies

- Feature 1 depends on merged PR #323 and the current direct-worker preparation path in `spawnlib.py`.
- Feature 2 depends on Feature 1's safe launcher and access to a managed session capable of starting nested Codex.
- Feature 3 depends on two successful fresh-session Feature 2 executions and an explicit operator decision about cadence and cost.
- All features depend on the existing run-record sanitation and lifecycle contract; none may embed secrets as evidence.

## Sequencing

1. Deliver `managed-codex-probe-contract` and verify production-path parity locally without invoking repository work.
2. Deliver `managed-codex-runtime-attestation` in the managed environment and repeat it from a fresh session.
3. Review reliability, cost, and diagnostic quality. Only then pick up `managed-codex-runtime-canary` through Route C.

Each feature enters Route C separately when selected. Do not spec all three up front.

## Risks and mitigations

- **Credential exposure:** use presence/usability assertions and redaction tests; never serialize credential values or credential-file contents.
- **False confidence from path drift:** assert that the launcher calls the same environment-preparation and spawn boundaries as direct workers, rather than reimplementing them.
- **Accidental repository work:** use a no-op contract, minimum roots, read-only target context, and post-run mutation checks.
- **Provider ambiguity:** record normalized provider/model identity supplied to and observed from the child, without recording auth material.
- **Flaky managed infrastructure:** classify stages, bound retries, and require two fresh-session passes before recurring use.
- **Unbounded cost or queue pressure:** keep the canary opt-in and low-frequency until measured; enforce time and retry ceilings.

## Release strategy

1. Release the safe launcher as an operator-invoked diagnostic, with unit/integration evidence and no default automation.
2. Execute and publish a sanitized one-shot managed attestation tied to the Worktrail commit under test; repeat from a fresh session.
3. If both runs are stable and diagnostics are actionable, introduce an advisory recurring canary.
4. Promote the canary to a release gate only through a later explicit policy decision backed by reliability and cost data.

Rollback is removal or disabling of the probe/canary surface; PR #323's worker isolation remains independently covered and is not rolled back merely because the managed probe infrastructure fails.
