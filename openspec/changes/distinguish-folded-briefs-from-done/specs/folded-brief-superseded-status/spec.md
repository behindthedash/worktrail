## Purpose

Distinguishes a brief that was absorbed (folded) into another brief from one
whose own work actually shipped, so a fold can no longer leave unshipped
work permanently stamped as completed.

## ADDED Requirements

### Requirement: Superseded closure mode stamps a distinct terminal status

The work-queue `done` operation SHALL accept a superseded-by closure mode
naming the surviving brief's id (`--superseded-by <brief-id>` on the CLI).
When given, it SHALL stamp `status: superseded` together with
`superseded-by: <brief-id>` and a `completed-at` timestamp, instead of
`status: done`, and SHALL report `superseded` as the closure's result
status. The mode SHALL be mutually exclusive with the planning-only and
implementation-complete closure modes.

#### Scenario: Closing a brief as superseded

- **WHEN** a claimed brief is closed with a superseded-by id
- **THEN** the brief's frontmatter carries `status: superseded`, a
  `superseded-by:` field naming that id, and a `completed-at` timestamp, and
  does not carry `status: done`
- **AND** the operation reports a `superseded` result status

#### Scenario: Superseded is mutually exclusive with the shipping closure modes

- **WHEN** a superseded-by closure is requested together with the
  planning-only or implementation-complete mode
- **THEN** the closure is refused with no mutation to the brief

#### Scenario: Ordinary closures are unaffected

- **WHEN** a brief is closed without a superseded-by id
- **THEN** it is stamped `status: done` exactly as before this change, with
  no `superseded-by:` field

### Requirement: Superseded closure is a non-shipping closure

A superseded-by closure asserts only that the brief's content is carried by
the surviving brief, not that its work shipped. It SHALL therefore satisfy
the Route-C continue-versus-planning-only decision gate without a
planning-only or implementation-complete declaration, and SHALL waive the
consolidation-batch closure evidence gate, matching the existing
duplicate-of closure's treatment.

#### Scenario: Route-C brief closed as superseded

- **WHEN** a brief whose recommended route is C is closed with a
  superseded-by id and neither shipping mode
- **THEN** the closure succeeds rather than being refused for lacking an
  explicit continue-versus-planning-only decision

#### Scenario: Consolidation-batch brief closed as superseded

- **WHEN** a brief that itself carries a consolidated-from section is closed
  with a superseded-by id and no note evidencing its sub-items
- **THEN** the closure succeeds rather than being refused as an unverified
  consolidation closure

#### Scenario: The evidence gates still apply to shipping closures

- **WHEN** a consolidation-batch brief is closed without a superseded-by id
  and without a note evidencing every sub-item
- **THEN** the closure is still refused as an unverified consolidation
  closure, unchanged from before this change

### Requirement: Superseded briefs are terminal

Wherever the work queue treats `status: done` as meaning a brief is closed
and no longer open work, it SHALL treat `status: superseded` the same way, so
a folded brief is never resurfaced as outstanding work.

#### Scenario: A superseded sibling is not reported as still open

- **WHEN** a brief is closed and one of its related siblings carries
  `status: superseded`
- **THEN** that sibling is not reported among the related briefs still open

#### Scenario: A superseded dependency reference resolves as satisfied

- **WHEN** a brief's dependency reference resolves to a picked brief
  carrying `status: superseded`
- **THEN** the reference resolves to the same closed/satisfied state a
  `status: done` brief resolves to, not to an active one

#### Scenario: Superseded briefs are excluded from duplicate scoring

- **WHEN** candidate briefs are scored for overlap against a new brief
- **THEN** a candidate carrying `status: superseded` is excluded from
  scoring exactly as a `status: done` candidate is

#### Scenario: Superseded briefs are not returned as open drift briefs

- **WHEN** the spec-sync dedup lookup searches the picked briefs for an
  existing open brief covering a drift source
- **THEN** a brief carrying `status: superseded` is skipped exactly as a
  `status: done` brief is

### Requirement: Cluster consolidation closes absorbed members as superseded

When cluster consolidation absorbs a member brief into a newly authored
consolidated brief, it SHALL close that member through the superseded-by
closure mode naming the consolidated brief's id, and SHALL NOT close it as
`done`. A member is counted as completed only when that superseded closure
succeeds.

#### Scenario: An absorbed member is stamped superseded, not done

- **WHEN** cluster consolidation successfully claims a member brief and
  folds it into the consolidated brief
- **THEN** the member is closed with a superseded-by id equal to the
  consolidated brief's id, and its frontmatter ends up carrying `status:
  superseded` rather than `status: done`
- **AND** the member's body still carries the `## Superseded` note
  referencing the consolidated brief, unchanged from before this change

#### Scenario: A failed superseded closure skips the member

- **WHEN** the superseded closure of a claimed member does not report a
  `superseded` result status
- **THEN** that member is recorded as skipped rather than completed, and the
  rest of the batch is still processed, exactly as for a failed closure
  before this change

#### Scenario: A nested consolidation batch keeps its provenance note

- **WHEN** the member being absorbed is itself a consolidation batch
  carrying its own consolidated-from section
- **THEN** the superseded closure still carries the nested-batch closure
  note listing that member's own sub-items
