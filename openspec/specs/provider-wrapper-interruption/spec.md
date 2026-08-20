## Purpose

Defines deterministic, provider-neutral lifecycle behavior when seeded skill dispatch is interrupted at either the wrapper or provider-child process boundary.

## Requirements

### Requirement: Interruption produces a recoverable terminal run record
Seeded skill dispatch SHALL finish the exact parent run record as `failed_recoverable` when SIGTERM is delivered either to the dispatch wrapper or directly to its provider child. The terminal record SHALL preserve the seeded dispatch ownership identity and SHALL describe interruption rather than successful completion.

#### Scenario: Provider child receives SIGTERM
- **WHEN** SIGTERM is delivered directly to a seeded dispatch provider child
- **THEN** the exact parent run record reaches `failed_recoverable` with interruption evidence and its dispatch ownership remains unchanged

#### Scenario: Dispatch wrapper receives SIGTERM
- **WHEN** SIGTERM is delivered to the wrapper supervising a seeded dispatch provider child
- **THEN** the exact parent run record reaches `failed_recoverable` with interruption evidence and its dispatch ownership remains unchanged

### Requirement: Interruption has a provider-neutral wrapper outcome
The dispatch wrapper MUST expose the same deterministic interrupted exit outcome for wrapper-targeted and child-targeted SIGTERM across Claude, Codex, and OpenCode, and that outcome MUST be non-successful.

#### Scenario: Compare interruption boundaries across providers
- **WHEN** each supported provider is interrupted once at the wrapper boundary and once at the child boundary
- **THEN** every dispatch wrapper exits with the same documented non-success outcome

### Requirement: Interrupted dispatch leaves no provider child
The dispatch wrapper SHALL terminate and reap its provider child before returning from an interrupted seeded dispatch, regardless of which interruption boundary was targeted.

#### Scenario: Wrapper interruption cleans up its child
- **WHEN** the dispatch wrapper receives SIGTERM while its fake provider child is running
- **THEN** the wrapper returns only after the fake provider child is no longer alive

#### Scenario: Direct child interruption is reaped
- **WHEN** the fake provider child receives SIGTERM directly
- **THEN** the dispatch wrapper reaps that child and returns without leaving an orphan process

### Requirement: Lifecycle regression remains hermetic
Provider interruption lifecycle coverage MUST use only fake provider executables and fake credentials, and MUST place every mutable test artifact under a `TemporaryDirectory`.

#### Scenario: Run the lifecycle matrix
- **WHEN** the interruption lifecycle regression runs for Claude, Codex, and OpenCode
- **THEN** it performs no real provider authentication or network work and leaves no mutable state outside its temporary root
