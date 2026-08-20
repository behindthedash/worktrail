## Context

The lifecycle harness added through PR #533 launches the installed `worktrail-skill-dispatch` wrapper around a fake executable named for each supported provider. Its interrupted case currently sends SIGTERM directly to the PID published by that fake provider; the wrapper itself is only a blocking `subprocess.run` caller and has no explicit interruption contract. The seeded child owns the run-record transition, so wrapper-targeted termination must reach that child before the wrapper exits. See `proposal.md` and `specs/provider-wrapper-interruption/spec.md` for the behavioral scope.

## Goals / Non-Goals

**Goals:**

- Make wrapper-targeted and provider-child-targeted SIGTERM converge on one observable exit and run-record result.
- Ensure wrapper shutdown forwards termination, waits for the provider child, and cannot strand it.
- Exercise the complete matrix through fake Claude, Codex, and OpenCode executables with isolated state.

**Non-Goals:**

- Changing provider command construction, provider selection, or seeded prompt contents.
- Exercising real provider binaries, credentials, networks, or provider-specific signal behavior.
- Defining behavior for uncatchable SIGKILL or redesigning general orchestrator worker cancellation.

## Decisions

### Supervise the provider process explicitly in the dispatch wrapper

Replace the opaque blocking launch with explicit child-process supervision for the execution path. While the child is active, the wrapper handles SIGTERM, forwards it to the child, waits for child termination, and maps the interrupted lifecycle to the documented shell-style exit outcome of `130`. Direct child SIGTERM already flows through the fake child's lifecycle handler and returns `130`, so this makes both boundaries converge without provider-specific branches.

An alternative is to rely on shell/process-group behavior. That is rejected because the wrapper is launched directly, process-group membership depends on the caller, and terminating one PID does not portably establish run-record completion or reaping.

### Keep terminal run-record ownership in the seeded child

The seeded executor remains responsible for writing `failed_recoverable` before it exits. The wrapper forwards the interrupt and waits rather than independently editing the run record. This preserves the existing single-owner lifecycle and avoids competing terminal writes.

An alternative is for the wrapper to parse seeded arguments and finish the run record itself. That duplicates executor semantics in a transport adapter and risks divergent ownership checks.

### Extend the existing lifecycle matrix with an explicit signal target

Parameterize the PR #533 lifecycle helper by interruption boundary. The harness will publish both wrapper and fake-child identities, send SIGTERM to the selected target, assert exit `130`, inspect the exact parent record, and prove the child PID is gone before temporary cleanup. The fake child will retain a SIGTERM handler that records `failed_recoverable`, making wrapper forwarding observable.

An alternative is a narrow mocked `subprocess` unit test. That would be smaller but could not prove real signal delivery, process reaping, installed entry-point behavior, or parity among generated provider commands.

### Isolate all test inputs and state

Every shim, fake skill, fake credential/home, run record, readiness marker, PID marker, and proof file will live below one `TemporaryDirectory`. The environment will point provider-specific authentication variables only at inert fake values when needed; no real user configuration is copied or mutated.

## Risks / Trade-offs

- [Signal handlers are process-global and can leak across in-process tests] → Exercise wrapper behavior through a subprocess and restore any temporary handler in implementation-level paths where reuse is possible.
- [A child may exit between the wrapper receiving SIGTERM and forwarding it] → Treat missing-process races as already terminated, still wait/reap, and preserve the deterministic interrupted outcome.
- [A child that ignores SIGTERM could make the wrapper wait indefinitely] → Keep this change scoped to the cooperative provider contract exercised by the fake child; any escalation timeout requires a separate policy decision.
- [PID reuse can make an orphan assertion misleading] → Pair PID liveness checks with process completion/readiness evidence and keep the assertion window within the temporary harness lifecycle.

## Migration Plan

Ship the wrapper supervision change and regression together. No stored data migration is required. Rollback consists of reverting both changes; existing non-interrupted dispatch command construction remains unchanged.
