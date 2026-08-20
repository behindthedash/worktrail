## Why

PR #533 proves seeded dispatch lifecycle outcomes when the fake provider child receives SIGTERM, but it does not define or verify what happens when the `worktrail-skill-dispatch` wrapper is interrupted instead. Without a provider-neutral boundary contract, wrapper termination can leave inconsistent run records, exit outcomes, or orphaned provider children across Claude, Codex, and OpenCode.

## What Changes

- Define one terminal-state contract for interruption at either the dispatch wrapper or provider-child boundary.
- Require seeded dispatch interruption to deterministically finish the parent run record as `failed_recoverable` and return a consistent non-success wrapper outcome across all supported providers.
- Require wrapper interruption to terminate and reap its provider child so no orphan fake process remains.
- Extend the fake-provider lifecycle regression with explicit wrapper-targeted and child-targeted SIGTERM cases using fake credentials and `TemporaryDirectory`-scoped mutable state.
- Run focused lifecycle coverage and the repository pre-PR verification gate.

## Capabilities

### New Capabilities

- `provider-wrapper-interruption`: Provider-neutral seeded-dispatch interruption behavior at wrapper and provider-child process boundaries.

### Modified Capabilities

None.

## Impact

The change affects the `worktrail-skill-dispatch` subprocess lifecycle, the seeded internal-dispatch fake-provider harness, and router lifecycle tests for Claude, Codex, and OpenCode. It introduces no real provider credentials, external mutable state, or public dependency changes.
