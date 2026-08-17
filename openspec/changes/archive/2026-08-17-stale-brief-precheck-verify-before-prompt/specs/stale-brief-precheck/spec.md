## MODIFIED Requirements

### Requirement: Evidence Is Surfaced To The Operator, Never Auto-Applied

When the check reports evidence, the system SHALL present the matching commits and pull
requests to the operator and SHALL require an explicit operator decision before either closing
the brief or continuing the dispatch, **except** in either of two carve-outs:

1. **A deterministic staleness predicate re-check** (see Requirement: Deterministic Predicate
   Re-Check Precedes Evidence Surfacing) has already determined the outcome for the brief — in
   that case the probe-based evidence this requirement governs is never computed or surfaced,
   and this requirement's operator-decision gate does not apply to that brief.
2. **A file-state verification step** (see Requirement: File-State Verification Precedes
   Evidence Surfacing) has classified the probe-based evidence as verifiably absent or
   verifiably present against current file state — in that case the dispatch proceeds or the
   brief closes automatically per that verification outcome, and this requirement's
   operator-decision gate does not apply to that brief.

The system SHALL NOT close, stamp, move, or otherwise mutate the brief on the basis of this
check alone, except via one of those two carve-outs. Brief lifecycle mutations SHALL continue
to be performed only through the work-queue owner, and only as the result of an explicit
operator choice, an automatic predicate re-check outcome, or an automatic file-state
verification outcome.

#### Scenario: Evidence prompts the operator rather than closing the brief
- **WHEN** the check reports one or more matching commits for a claimed Route-F brief
- **THEN** the operator is shown the evidence and asked whether the brief is already delivered,
  and no queue mutation occurs until that answer is given

#### Scenario: Operator may proceed despite evidence
- **WHEN** the operator judges the surfaced evidence to be unrelated or only a partial delivery
- **THEN** the dispatch proceeds unchanged, and the run record records both the surfaced
  evidence and the operator's decision to continue

#### Scenario: No evidence produces no prompt
- **WHEN** the check reports `checked: true` with no matches, or reports `checked: false`
- **THEN** no operator prompt is shown and the dispatch proceeds without interruption

#### Scenario: A recognized deterministic predicate bypasses the operator prompt
- **WHEN** a claimed brief carries a `drift-source` predicate the system recognizes, and the
  predicate re-check for that brief completes without error
- **THEN** the operator is never prompted with probe-matched evidence for this brief; the
  predicate re-check's own outcome decides whether the brief is closed or the dispatch proceeds,
  and this requirement's evidence-surfacing and operator-decision behavior does not run for it

#### Scenario: A conclusive file-state verification bypasses the operator prompt
- **WHEN** the check reports one or more matching commits or pull requests for a claimed brief,
  the predicate re-check did not already determine the outcome, and the file-state verification
  step classifies the brief's requested capability as verifiably absent or verifiably present
- **THEN** the operator is never prompted with the probe-matched evidence for this brief; the
  verification outcome decides whether the dispatch proceeds or the brief is closed, and this
  requirement's evidence-surfacing and operator-decision behavior does not run for it

## ADDED Requirements

### Requirement: File-State Verification Precedes Evidence Surfacing

For every brief-sourced dispatch where the probe-based staleness search
(`check_brief_staleness`) reports one or more matching commits or pull requests, and the
deterministic predicate re-check did not already determine the outcome, the system SHALL
attempt to verify — by reading or grepping the brief's named paths and symbols — whether the
specific capability the brief's focus prose describes is currently present in, or absent from,
the matched files' current content. This verification SHALL classify the result as exactly one
of **verifiably absent**, **verifiably present**, or **inconclusive**, and SHALL run identically
regardless of whether the dispatch is interactive or `AUTO_MODE`, since it is an internal
reasoning step, not a human-facing prompt. When the classification is inconclusive, or the
verification step itself cannot be completed, the system SHALL fall through unchanged to
today's operator-prompt (interactive) or decision-record-plus-release (`AUTO_MODE`) flow,
exactly as if no verification had been attempted.

#### Scenario: Verification runs only when probe-based evidence exists
- **WHEN** the probe-based search reports `checked: true` with no matches and no pull requests
- **THEN** no file-state verification is attempted, because there is no evidence to verify
  against

#### Scenario: Verification is skipped when the predicate re-check already decided
- **WHEN** a claimed brief's deterministic predicate re-check already determined the dispatch
  outcome
- **THEN** the probe-based search and the file-state verification step do not run for that
  brief, consistent with Requirement: Deterministic Predicate Re-Check Precedes Evidence
  Surfacing

#### Scenario: Verification runs the same way in AUTO_MODE as interactively
- **WHEN** the probe-based search reports evidence for an `AUTO_MODE` dispatch
- **THEN** the file-state verification step is attempted before any decision-record is filed,
  exactly as it would be attempted before an interactive operator prompt

#### Scenario: An inconclusive classification falls through unchanged
- **WHEN** the file-state verification step cannot determine, from the matched files' current
  content, whether the brief's requested capability is present or absent
- **THEN** the classification is inconclusive, and the dispatch proceeds to today's unmodified
  operator-prompt or decision-record-plus-release flow, exactly as if no verification had run

#### Scenario: A verification failure falls through unchanged
- **WHEN** the file-state verification step cannot complete (for example, a named path no
  longer exists to read)
- **THEN** the outcome is treated as inconclusive, no auto-proceed or auto-close occurs on its
  basis, and the dispatch falls through to today's unmodified operator-prompt or
  decision-record-plus-release flow

### Requirement: Verified Absent Proceeds Automatically With Recorded Verification

When the file-state verification step classifies the brief's requested capability as verifiably
absent from current file state, the system SHALL proceed with the dispatch automatically,
without prompting the operator and without filing a decision record, and SHALL record both the
probe-based evidence and the verification finding on the run record once it is opened. The
system SHALL NOT omit this recording; a verifiably-absent outcome SHALL always be recorded,
never silently skipped.

#### Scenario: A verifiably-absent capability proceeds without a prompt
- **WHEN** the probe-based search surfaces a merged pull request touching a brief's named file,
  and the file-state verification step confirms the brief's specific requested capability is
  still absent from that file's current content
- **THEN** the dispatch proceeds to Phase 6/7 without an operator prompt and without a filed
  decision record, in both interactive and `AUTO_MODE` dispatches

#### Scenario: The verified-absent outcome is recorded on the run record
- **WHEN** the file-state verification outcome is verifiably absent and Phase 6 opens the run
  record for the proceeding dispatch
- **THEN** the run record carries an entry naming both the probe-based evidence (matched
  commits or pull requests) and the verification finding that the requested capability remains
  absent, and this entry is never omitted

### Requirement: Verified Present Closes The Brief Automatically Citing The Verification

When the file-state verification step classifies the brief's requested capability as verifiably
present in current file state, the system SHALL close the brief automatically as
already-delivered through the work-queue owner, before Phase 6 opens a run record, citing both
the probe-based evidence and the verification finding as the reason for closure.

#### Scenario: A verifiably-present capability closes the brief automatically
- **WHEN** the probe-based search surfaces a merged pull request touching a brief's named file,
  and the file-state verification step confirms the brief's specific requested capability is
  already present in that file's current content
- **THEN** the brief is closed as already-delivered automatically, with no operator prompt and
  no dispatch to Phase 6/7, in both interactive and `AUTO_MODE` dispatches

#### Scenario: The closure note cites both the probe evidence and the verification finding
- **WHEN** the system closes a brief under this requirement
- **THEN** the closure note names the probe-based evidence (matched commits or pull requests)
  and the verification finding that confirmed the requested capability's presence
