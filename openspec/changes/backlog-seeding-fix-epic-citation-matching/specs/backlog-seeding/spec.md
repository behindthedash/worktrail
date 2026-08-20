## MODIFIED Requirements

### Requirement: Epics with unspecced features are seeded by citation gap

For each epic file `docs/specs/epics/<NNN>-<slug>.md` whose `**Status:**` line is not terminal,
the seeder SHALL count `### Feature` headings and the spec folders whose top-level markdown
cites the epic. A spec/change folder counts as citing the epic when any of its top-level
markdown files matches at least one of: the epic's literal id string (e.g.
`002-safe-work-queue-dependency-references`); `Epic <NNN> Feature <M>` prose, where `<NNN>` is
the epic id's leading three-digit number and `<M>` is any digit sequence (case-insensitive); or
any of the epic's own documented `**Future spec id:**` values. When citations are fewer than
features, it SHALL capture one planning-only Route C brief whose focus directs the picking
session to spec the next unspecced feature — or, when the decomposition is in fact complete, to
flip the epic's `**Status:**` line to a terminal value so seeding stops. An epic with no
parseable `### Feature` headings SHALL be reported and never seeded (without a feature count
there is no terminal condition). Files in `epics/` that do not match the `NNN-slug` naming
pattern SHALL be ignored.

#### Scenario: An epic decomposes into more features than have citing specs
- **WHEN** an epic with a non-terminal status has 2 `### Feature` headings and 1 spec citing its
  id
- **THEN** a queued brief is created with `seeded-from: <repo>:epic:<epic-id>:cited=1`

#### Scenario: Every decomposed feature has a citing spec
- **WHEN** an epic's citing-spec count is greater than or equal to its feature count
- **THEN** no brief is created for that epic

#### Scenario: The epic's status line is terminal
- **WHEN** an epic's `**Status:**` line matches a terminal value (e.g. Completed, Superseded)
- **THEN** no brief is created regardless of its citation gap

#### Scenario: A change cites the epic by "Epic NNN Feature M" prose, not the literal id
- **WHEN** an epic `002-safe-work-queue-dependency-references` has 2 decomposed features, one
  spec cites its literal id, and a second change's `proposal.md` reads "Epic 002 Feature 2 adds
  this conservative runtime boundary" without ever containing the literal epic id string
- **THEN** both count as citing specs and no brief is created — the epic is fully cited

#### Scenario: A change cites the epic only by its documented future spec id
- **WHEN** an epic's Feature 1 section documents `**Future spec id:** `payments-core-ledger``
  and a change folder's markdown contains `payments-core-ledger` but never the epic's literal id
  string or "Epic NNN Feature M" prose
- **THEN** that change counts as a citing spec for the epic

#### Scenario: A bare mention of the epic number without "Feature" is not a citation
- **WHEN** a spec/change folder's markdown contains the epic's leading number in an unrelated
  context (e.g. "see PR 002" or "002 open items") but no literal epic id, no "Epic NNN Feature
  M" phrase, and no documented future spec id
- **THEN** that folder does not count as a citing spec
