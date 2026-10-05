## Purpose

Defines the non-terminal record semantics of a checkpointed landing after a
pull request has already merged, independently of the scope-review finish gate.

## ADDED Requirements

### Requirement: Checkpointed merged landing preserves an active run record

When a checkpointed PR landing observes that the pull request is already
merged, the landing pipeline SHALL report `completed_and_merged` as its
classified outcome and append that outcome to the run record's decisions. It
SHALL NOT finish the run record, append a scope-review entry on the caller's
behalf, or require the caller-supplied record to satisfy the scope-review
completion gate. This checkpoint behavior SHALL be limited to recording the
observed landing outcome; it SHALL NOT alter the corresponding non-checkpoint
completion behavior.

#### Scenario: Caller-supplied run has no scope review

- **WHEN** a checkpointed landing observes an already merged PR for a
  caller-supplied run record that has no scope-review entries
- **THEN** it reports `completed_and_merged`, appends a decision recording the
  merged outcome, and leaves the run record non-terminal without a
  scope-review-gate refusal

#### Scenario: Non-checkpoint merged landing

- **WHEN** a non-checkpoint landing observes an already merged PR
- **THEN** it retains the existing terminal `completed_and_merged` completion
  behavior, including its normal scope-review requirements
