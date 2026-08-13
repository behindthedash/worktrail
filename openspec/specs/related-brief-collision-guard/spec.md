# related-brief-collision-guard Specification

## Purpose
TBD - created by archiving change related-brief-collision-guard. Update Purpose after archive.
## Requirements
### Requirement: Related-Brief Extraction

The system SHALL read the `related:` frontmatter list from a claimed brief
and, for each listed identifier, resolve it against the personal work
queue's `picked/` and `queue/` directories using the same identifier
resolution rules as `work_queue.py resolve` (full filename, stem, leading
prefix, stem suffix, then `id` frontmatter). An identifier that resolves to
no file, or ambiguously to more than one, SHALL be skipped for that entry
and SHALL NOT abort the check for the brief's other `related:` entries.

#### Scenario: A related id resolves to a picked brief
- **WHEN** the claimed brief's `related:` list contains an id that resolves
  to exactly one file in `picked/`
- **THEN** that file is read for its claim status

#### Scenario: An unresolvable related id is skipped, not fatal
- **WHEN** a `related:` id resolves to zero or more than one file
- **THEN** that entry is skipped and the check continues evaluating the
  brief's remaining `related:` entries

#### Scenario: No related entries yields no findings
- **WHEN** the claimed brief has no `related:` frontmatter field, or an
  empty one
- **THEN** the check reports `checked: true` with an empty `active` list,
  without inspecting the queue directories at all

### Requirement: Active-Claim Determination

For each resolved related brief found in `picked/`, the system SHALL treat
it as actively claimed when its frontmatter `status` field is `picked` (not
`done`). The system SHALL report the related brief's id, `claimed-by`,
`claimed-at`, `repo`, and a truncated focus summary for each active match.
A related brief resolved in `queue/` (not yet claimed by anyone) SHALL NOT
be reported as an active match.

#### Scenario: A picked, unclaimed-since-done related brief is active
- **WHEN** a related brief resolves in `picked/` with `status: picked`
- **THEN** it is reported as an active match with its `claimed-by` and
  `claimed-at` values

#### Scenario: A done related brief is not active
- **WHEN** a related brief resolves in `picked/` with `status: done`
- **THEN** it is not reported as an active match

#### Scenario: A still-queued related brief is not active
- **WHEN** a related brief resolves only in `queue/`
- **THEN** it is not reported as an active match, because nobody has
  claimed it yet

### Requirement: Local Run-Record Enrichment Is Best-Effort

When an active match's `claimed-by` value matches the local machine's own
agent label, the system SHALL attempt to find a local GO run record under
`~/.worktrail/runs/<repo-name>/` referencing the related brief's id, and SHALL
include its path in the match when found. The absence of a matching run
record SHALL NOT change whether the match is reported as active, and any
failure reading the run-record directory SHALL be silently ignored.

#### Scenario: A same-machine active claim is enriched with its run record
- **WHEN** an active match's `claimed-by` matches this machine's agent
  label and a local run record under `~/.worktrail/runs/<repo>/` names the related
  brief's id
- **THEN** the match includes that run record's path

#### Scenario: Missing run record does not suppress the match
- **WHEN** an active match's `claimed-by` matches this machine's agent
  label but no local run record references the related brief's id
- **THEN** the match is still reported as active, without a run-record path

### Requirement: The Check Fails Open

The system SHALL never raise to its caller. Any condition under which the
check cannot be completed -- an unreadable brief, a missing or inaccessible
queue directory, malformed frontmatter, or any other read failure -- SHALL
yield a result whose `checked` field is `false`, with a non-null warning,
rather than an exception or a partial result presented as complete.

#### Scenario: Missing queue directory yields checked false
- **WHEN** the work queue's `picked/` directory does not exist or is
  unreadable
- **THEN** the check returns `checked: false` with a warning, and raises
  nothing

#### Scenario: An unreadable claimed-brief file yields checked false
- **WHEN** the claimed brief passed to the check cannot be read or parsed
- **THEN** the check returns `checked: false` with a warning naming the
  read failure

### Requirement: Route Gate Covers Routes Not Already Handled

The system SHALL run this check during `/go` Phase 5.5 only when the
dispatch is brief-sourced, the claimed brief carries at least one
`related:` entry, and the resolved route is not `C`, `D`, `E`, or `F` (the
routes already covered by the existing spec-collision and brief-staleness
branches). A free-text dispatch with no claimed brief SHALL skip this check
entirely, as SHALL a brief-sourced dispatch with no `related:` entries.

#### Scenario: Brief-sourced Route I dispatch with related entries runs the check
- **WHEN** a claimed brief carrying `related:` entries is dispatched and
  Phase 5 resolves route `I`
- **THEN** this check runs before Phase 6 opens the run record

#### Scenario: Route D dispatch is unaffected
- **WHEN** a brief-sourced dispatch resolves to route `D`
- **THEN** this check does not run, because route D is already covered by
  the spec-collision branch

#### Scenario: A brief with no related entries skips the check regardless of route
- **WHEN** a claimed brief has no `related:` frontmatter field and resolves
  to route `H`
- **THEN** this check does not run, because there is nothing to check

### Requirement: Findings Are Surfaced To The Operator, Never Auto-Applied

When the check reports one or more active matches, the system SHALL present
all of them to the operator in a single prompt and SHALL require an
explicit operator decision (proceed, or pause to reconcile) before Phase 6
continues. The system SHALL NOT close, link, skip, or otherwise mutate
either brief on the basis of this check alone.

#### Scenario: Active matches produce one batched prompt
- **WHEN** the check reports two or more active matches for the same
  dispatch
- **THEN** the operator sees all matches in a single prompt, not one
  prompt per match

#### Scenario: Operator may proceed despite an active match
- **WHEN** the operator judges an active match to be non-overlapping in
  scope
- **THEN** the dispatch proceeds unchanged, and the run record records both
  the surfaced match and the operator's decision to continue

#### Scenario: No active matches produces no prompt
- **WHEN** the check reports `checked: true` with an empty `active` list,
  or reports `checked: false`
- **THEN** no operator prompt is shown and the dispatch proceeds without
  interruption

