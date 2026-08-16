## MODIFIED Requirements

### Requirement: Evidence Is Surfaced To The Operator, Never Auto-Applied

When the check reports evidence, the system SHALL present the matching commits and pull
requests to the operator and SHALL require an explicit operator decision before either closing
the brief or continuing the dispatch, **except when a deterministic staleness predicate
re-check (see Requirement: Deterministic Predicate Re-Check Precedes Evidence Surfacing) has
already determined the outcome for the brief** — in that case the probe-based evidence this
requirement governs is never computed or surfaced, and this requirement's operator-decision
gate does not apply to that brief. The system SHALL NOT close, stamp, move, or otherwise
mutate the brief on the basis of this check alone, except via that predicate re-check carve-out.
Brief lifecycle mutations SHALL continue to be performed only through the work-queue owner, and
only as the result of an explicit operator choice or an automatic predicate re-check outcome.

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

## ADDED Requirements

### Requirement: Deterministic Staleness Predicate Is Captured On Sweep-Generated Briefs

A brief filed by a sweep that can express its triggering condition as a deterministic,
machine-checkable predicate SHALL stamp that predicate's re-runnable inputs onto the brief as
structured frontmatter, keyed by the same `drift-source` marker value the sweep already writes.
For the checkbox-drift-sweep, the structured frontmatter SHALL be a `drift-findings` list with
one entry per captured hit, and each entry SHALL carry at minimum the hit's task-file `path`
relative to the repository root. This is in addition to, and SHALL NOT replace, the existing
human-readable prose rendering of the same findings in the brief body.

#### Scenario: A checkbox-drift brief carries structured, re-runnable findings
- **WHEN** the checkbox-drift-sweep files a brief for one or more `status: completed` task
  files with unchecked body checkboxes
- **THEN** the brief's frontmatter includes a `drift-findings` list with each affected task
  file's path, alongside the existing `drift-source: checkbox-drift-sweep` marker and the
  existing prose bullet list in the body

#### Scenario: Missing structured findings is treated as no predicate
- **WHEN** a brief carries `drift-source: checkbox-drift-sweep` but no `drift-findings`
  frontmatter (for example, a brief captured before this requirement existed)
- **THEN** a predicate re-check attempted against that brief cannot determine an outcome and the
  dispatch falls through to the unmodified probe-based staleness flow

### Requirement: Deterministic Predicate Re-Check Precedes Evidence Surfacing

For every brief-sourced dispatch, before the probe-based staleness search
(`check_brief_staleness`) runs or any evidence is surfaced to the operator, the system SHALL
check whether the claimed brief carries a `drift-source` value it recognizes and a non-empty
`drift-findings` list. When both are present, the system SHALL re-run that predicate's check
against the current base branch, restricted to the captured `drift-findings` entries, and SHALL
use the outcome of that re-check to decide the dispatch, bypassing the probe-based search and
operator prompt entirely. When either is absent, or the recognized predicate's re-check itself
fails (an unreadable or unparseable task file, or any other error), the system SHALL fall
through unchanged to today's probe-based `check_brief_staleness` search and operator-prompt
flow, exactly as if no predicate had been attempted.

#### Scenario: Recognized predicate with captured findings runs first
- **WHEN** a claimed brief carries `drift-source: checkbox-drift-sweep` and a non-empty
  `drift-findings` list
- **THEN** the predicate re-check runs before `check_brief_staleness` and before any operator
  prompt, and its outcome — not the probe-based search — decides the dispatch

#### Scenario: No drift-source falls through unchanged
- **WHEN** a claimed brief carries no `drift-source` frontmatter field
- **THEN** no predicate re-check is attempted and the dispatch proceeds through the unmodified
  probe-based `check_brief_staleness` search and operator-prompt flow

#### Scenario: Unrecognized drift-source falls through unchanged
- **WHEN** a claimed brief carries a `drift-source` value the system does not recognize
- **THEN** no predicate re-check is attempted for that value and the dispatch proceeds through
  the unmodified probe-based flow

#### Scenario: A re-check error falls through unchanged
- **WHEN** a recognized predicate's re-check raises or cannot read one of its captured
  `drift-findings` entries (for example, the task file no longer exists at the captured path)
- **THEN** the predicate re-check outcome is treated as inconclusive, no auto-proceed or
  auto-close occurs on its basis, and the dispatch falls through to the unmodified probe-based
  `check_brief_staleness` search and operator-prompt flow

### Requirement: Predicate Still True Proceeds Automatically With Recorded Evidence

When a predicate re-check completes without error and finds that the condition which generated
the brief still holds for at least one captured finding, the system SHALL proceed with the
dispatch automatically, without prompting the operator, and SHALL record the predicate
re-check's result — including which findings still hold — on the run record once it is opened.
The system SHALL NOT omit this recording; a predicate re-check outcome of "still true" SHALL
always be recorded, never silently skipped.

#### Scenario: A still-drifted finding proceeds without a prompt
- **WHEN** a checkbox-drift brief's predicate re-check finds that one of its captured task
  files is still `status: completed` with an unchecked body checkbox
- **THEN** the dispatch proceeds to Phase 6/7 without an operator prompt

#### Scenario: A partially-resolved brief is still treated as true
- **WHEN** a checkbox-drift brief captured findings for two task files, and the re-check finds
  one file's checkboxes now fully checked but the other file still `status: completed` with an
  unchecked checkbox
- **THEN** the predicate re-check outcome is "still true" (proceed), because the condition that
  generated the brief — drift exists in this repository's captured scope — still holds

#### Scenario: The still-true outcome is recorded on the run record
- **WHEN** the predicate re-check outcome is "still true" and Phase 6 opens the run record for
  the proceeding dispatch
- **THEN** the run record carries an entry naming the predicate re-check and the findings that
  still hold, and this entry is never omitted

### Requirement: Predicate Resolved Closes The Brief Automatically Citing The Re-Check

When a predicate re-check completes without error and finds that the condition which generated
the brief no longer holds for any captured finding, the system SHALL close the brief
automatically as already-delivered through the work-queue owner, before Phase 6 opens a run
record, citing the predicate re-check result itself as the reason for closure. The system SHALL
NOT cite matched commits or merged pull requests as the reason for this closure, because such
matches are not computed for a brief resolved this way and, even when coincidentally available,
are not proof that the predicate's own condition was what changed.

#### Scenario: All captured findings resolved closes the brief
- **WHEN** a checkbox-drift brief's predicate re-check finds that every captured task file is
  either no longer `status: completed` or has every audited checkbox now checked
- **THEN** the brief is closed as already-delivered automatically, with no operator prompt and
  no dispatch to Phase 6/7

#### Scenario: The closure note cites the predicate re-check, not commit/PR evidence
- **WHEN** the system closes a brief under this requirement
- **THEN** the closure note names the predicate re-check (which findings were re-verified as
  resolved) and does not name or rely on any matched commit SHA or pull-request number as the
  reason for closure

### Requirement: Checkbox-Drift Predicate Re-Check Reflects Current Task-File State

For `drift-source: checkbox-drift-sweep`, the predicate re-check SHALL determine each captured
finding's current state by reading that finding's task file directly from the current base
branch, not by reusing the counts captured when the brief was filed. A finding SHALL be
classified as still holding when its task file is currently `status: completed` with at least
one unchecked checkbox in the audited completion sections, and as resolved when the file is
currently `status: completed` with every audited checkbox checked, or is no longer `status:
completed` at all. A finding whose task file cannot be read (missing, moved, or unparseable)
SHALL cause the whole predicate re-check for that brief to be treated as an error, per the
Requirement: Deterministic Predicate Re-Check Precedes Evidence Surfacing.

#### Scenario: A finding's current checkbox state overrides its captured counts
- **WHEN** a captured finding recorded 3 unchecked checkboxes at capture time, and re-reading
  the task file now shows 0 unchecked checkboxes
- **THEN** the finding is classified as resolved, based on the current read, not the captured
  count

#### Scenario: A moved or deleted task file errors the whole re-check
- **WHEN** a captured finding's `path` no longer exists on the current base branch
- **THEN** the predicate re-check for that brief is treated as an error and falls through to
  the unmodified probe-based flow, rather than being classified as resolved
