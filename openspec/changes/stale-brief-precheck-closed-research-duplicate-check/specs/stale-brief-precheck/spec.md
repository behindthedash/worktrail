## ADDED Requirements

### Requirement: Backward-Looking Research-Note Search Complements The History Search

In addition to the forward-looking base-branch history search, the system SHALL search
`docs/specs/research/*.md` notes on the resolved base branch for textual overlap with the
brief's path and symbol probes (the same probes the forward-looking search uses), restricted to
notes touched within a **research lookback window**: from the brief's capture timestamp minus a
fixed, documented number of days (`RESEARCH_LOOKBACK_DAYS`) to the brief's capture timestamp
plus the same grace period used by the forward-looking search (`RACE_GRACE_SECONDS`). A note is
reported as a match when it textually contains, as a literal substring, one of the brief's path
or symbol probes; pull-request probes SHALL NOT be matched against research notes. This search
SHALL run independently of the forward-looking commit and pull-request search: its own failure
SHALL NOT change the result of the forward-looking search, and the forward-looking search's own
failure SHALL NOT suppress this search. The number of candidate notes considered and the number
of reported matches SHALL each be capped, with any content dropped by a cap counted rather than
silently discarded.

#### Scenario: A note published before the brief's capture is reported as evidence

- **WHEN** a research note documenting an investigation was committed to the base branch inside
  the research lookback window, before a brief's capture timestamp, and its content contains one
  of the brief's path or symbol probes
- **THEN** that note is reported as a research-note match, carrying the note's path, the matched
  probe, the kind of probe, and the note's last-touch commit sha and date

#### Scenario: A note outside the lookback window is not evidence

- **WHEN** the only research note whose content contains a brief's probe was last touched before
  the start of the research lookback window
- **THEN** it is not reported as a research-note match

#### Scenario: A note published moments after capture is still evidence

- **WHEN** a research note was committed to the base branch after a brief's capture timestamp
  but within the grace period the forward-looking search already applies
- **THEN** that note is reported as a research-note match, not silently excluded, mirroring how
  the forward-looking search treats a delivering commit landing moments before capture

#### Scenario: Pull-request probes are not matched against research notes

- **WHEN** a brief's only extracted probe is a pull-request number
- **THEN** the research-note search finds no matches, regardless of whether any note's content
  happens to mention that number

#### Scenario: Research-note search failure does not affect the history search

- **WHEN** the `git` calls the research-note search depends on fail or time out
- **THEN** the result's `matches` and `pull_requests` fields are unaffected, `research_notes` is
  empty, and a warning names the cause

#### Scenario: History search failure does not affect the research-note search

- **WHEN** the forward-looking history search's `git log` calls fail or time out
- **THEN** the research-note search still runs and its results are still reported

#### Scenario: Excess candidate notes or matches are counted, not silently dropped

- **WHEN** more research notes were touched within the lookback window than the candidate cap,
  or more matches were found than the match cap
- **THEN** the excess is reported in the warning, and the kept notes/matches are the
  most-recently-touched ones

### Requirement: Research-Note Evidence Reaches The Same Operator Prompt

A non-empty research-note match list SHALL trigger the same File-state-verification-then-
operator-prompt flow that a non-empty commit or pull-request match list already triggers, rather
than a separate prompt. The system SHALL NOT close, stamp, move, or otherwise mutate the brief
on the basis of a research-note match alone, subject to the same automatic-outcome carve-outs
(a deterministic predicate re-check, or a conclusive file-state verification) that already apply
to commit and pull-request evidence.

#### Scenario: A research-note match alone triggers file-state verification

- **WHEN** the check reports an empty `matches` list, an empty `pull_requests` list, and one or
  more `research_notes` matches for a claimed brief
- **THEN** file-state verification runs and, absent a conclusive verifiably-absent or
  verifiably-present classification, the operator is prompted with the research-note evidence

#### Scenario: No evidence of any kind produces no prompt

- **WHEN** the check reports `checked: true` with empty `matches`, `pull_requests`, and
  `research_notes`
- **THEN** no operator prompt is shown and the dispatch proceeds without interruption
