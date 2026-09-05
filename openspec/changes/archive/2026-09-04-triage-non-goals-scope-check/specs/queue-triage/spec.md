## ADDED Requirements

### Requirement: Fold candidates exclude scope-conflicting changes
When ranking a repo's active OpenSpec changes as `fold-into-change` candidates for a brief,
the ranking SHALL exclude a change that otherwise meets the minimum lexical-overlap floor
when that change's own declared Non-Goals or Out-of-scope text overlaps the brief's focus
tokens at or above the same floor coefficient. Non-Goals / Out-of-scope text SHALL be read
from the candidate's `proposal.md` and, when present, its `design.md`. A candidate with no
such declared text is unaffected by this exclusion.

#### Scenario: Candidate disclaiming the brief's topic is excluded
- **WHEN** a repo's active change scores at or above the minimum candidate floor against a
  brief's focus tokens on proposal-summary and task-line overlap alone, and that change's
  `proposal.md` or `design.md` declares a Non-Goals / Out-of-scope section whose text
  overlaps the same brief's focus tokens at or above the floor
- **THEN** that change is not returned as a fold candidate for the brief

#### Scenario: Unrelated Non-Goals text does not exclude a candidate
- **WHEN** a repo's active change scores at or above the minimum candidate floor against a
  brief's focus tokens, and that change declares a Non-Goals / Out-of-scope section whose
  text does not overlap the brief's focus tokens at or above the floor
- **THEN** that change is still returned as a fold candidate for the brief, ranked as before

#### Scenario: Candidate with no declared Non-Goals is unaffected
- **WHEN** a repo's active change scores at or above the minimum candidate floor and neither
  its `proposal.md` nor its `design.md` declares any Non-Goals / Out-of-scope section
- **THEN** that change is returned as a fold candidate exactly as it was ranked before this
  exclusion existed

#### Scenario: Excluded candidate is never offered to the evaluator
- **WHEN** a change is excluded from a brief's fold-candidate list because of a conflicting
  Non-Goals declaration
- **THEN** the evaluator prompt built for that brief does not list the excluded change as a
  candidate, and a `fold-into-change` verdict naming it is rejected as naming a change that
  was not presented
