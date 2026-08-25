## Purpose

Preserves per-iteration drain attribution and failure evidence so operators and downstream digests can diagnose blocked or failed work from durable run output.

## ADDED Requirements

### Requirement: Iteration summaries preserve structured diagnostic context

Each completed drain iteration SHALL include structured summary fields for its failure classification, non-negative claimed-queue delta, claimed-brief attribution count, and transcript path or equivalent durable diagnostic pointer. Fields SHALL remain present with explicit null or zero values when no failure, claim, attribution, or pointer exists, so consumers can distinguish absent evidence from an older or incomplete record.

#### Scenario: Blocked iteration has no attributed brief

- **WHEN** an iteration is classified as blocked by an agent failure and claims no brief
- **THEN** its summary entry identifies the failure class, records a zero claimed delta and zero claimed-brief count, retains a null brief attribution, and includes any available durable diagnostic pointer

#### Scenario: Failed iteration claimed one brief

- **WHEN** an iteration claims exactly one brief and is classified as failed
- **THEN** its summary entry retains the brief identifier, failure classification when one was detected, claimed delta, claimed-brief count, process exit code, outcome, and any available durable diagnostic pointer needed to investigate the failure without consulting the live process

#### Scenario: No transcript was configured or persisted

- **WHEN** an iteration completes without a persisted transcript or equivalent diagnostic artifact
- **THEN** the durable-pointer field is present with a null value while the remaining structured diagnostic fields still describe the available attribution and failure evidence

### Requirement: Human-readable iteration logs expose attribution evidence

The per-iteration drain log line SHALL report the failure classification, claimed delta, claimed-brief attribution count, and any durable diagnostic pointer alongside the existing outcome, brief, exit code, and elapsed time.

#### Scenario: Operator reads a blocked no-brief log line

- **WHEN** a blocked iteration has no attributed brief
- **THEN** its log line shows the classified failure and zero claim attribution rather than only `brief=-`

#### Scenario: Operator reads a failed claimed-brief log line

- **WHEN** a failed iteration has an attributed brief and a persisted transcript
- **THEN** its log line shows the brief, claim attribution context, failure classification, and transcript pointer together
