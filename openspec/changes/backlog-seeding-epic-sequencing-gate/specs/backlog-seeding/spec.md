## MODIFIED Requirements

### Requirement: Epics with unspecced features are seeded by citation gap

For each epic file `docs/specs/epics/<NNN>-<slug>.md` whose `**Status:**` line is not terminal,
the seeder SHALL count `### Feature` headings and the spec folders whose top-level markdown
cites the epic. A spec/change folder counts as citing the epic when any of its top-level
markdown files matches at least one of: the epic's literal id string (e.g.
`002-safe-work-queue-dependency-references`); `Epic <NNN> Feature <M>` prose, where `<NNN>` is
the epic id's leading three-digit number and `<M>` is any digit sequence (case-insensitive); or
any of the epic's own documented `**Future spec id:**` values. When citations are fewer than
features, the seeder SHALL determine the next unspecced feature's number as `cited + 1` and
check the epic's own text for a sequencing gate naming that feature before seeding it:

- A **pairwise gate** — text matching `Feature <cited + 1> depends on Feature <M>`
  (case-insensitive) for some `M` less than `cited + 1`.
- A **blanket gate** — text matching `Feature <M> gates the rest` / `... the remaining
  (features)` / `... later features` (case-insensitive) for some `M` less than `cited + 1`.

When one or more such gates are found, the seeder SHALL resolve each named gating feature `M` to
its own documented `**Future spec id:**` and check that spec's own dashboard stage. A gate is
**closed** only when its spec exists and its stage is `done` or `complete`; any other stage
(including the spec not existing yet) leaves the gate **open**. If every named gate for the next
unspecced feature is closed (or no gate is named at all), the seeder SHALL capture one
planning-only Route C brief whose focus directs the picking session to spec the next unspecced
feature — or, when the decomposition is in fact complete, to flip the epic's `**Status:**` line
to a terminal value so seeding stops. If any named gate is open, the seeder SHALL skip seeding
that feature this sweep and report it, the same way an unparseable epic is skipped and reported,
rather than presenting unready planning work as a brief. An epic with no parseable `### Feature`
headings SHALL be reported and never seeded (without a feature count there is no terminal
condition). Files in `epics/` that do not match the `NNN-slug` naming pattern SHALL be ignored.

#### Scenario: An epic decomposes into more features than have citing specs
- **WHEN** an epic with a non-terminal status has 2 `### Feature` headings and 1 spec citing its
  id, and the epic names no sequencing gate for Feature 2
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

#### Scenario: A pairwise sequencing gate is open
- **WHEN** an epic has 2 `### Feature` headings, 1 citing spec, its text reads "Feature 2 depends
  on Feature 1's contract," and Feature 1's own documented `**Future spec id:**` spec exists but
  is not in the `done` or `complete` dashboard stage
- **THEN** no brief is created for that epic this sweep, and it is reported the same way an
  unparseable epic is reported

#### Scenario: A blanket sequencing gate is open
- **WHEN** an epic has 3 `### Feature` headings, 1 citing spec, its text reads "Feature 1 gates
  the rest; no later feature is spec'd until its evidence closes," and Feature 1's own
  documented spec has 0 of its tasks complete
- **THEN** no brief is created for that epic this sweep, and it is reported the same way an
  unparseable epic is reported

#### Scenario: A named sequencing gate is closed
- **WHEN** an epic has 2 `### Feature` headings, 1 citing spec, its text reads "Feature 2 depends
  on Feature 1's contract," and Feature 1's own documented spec's dashboard stage is `done`
- **THEN** a queued brief is created with `seeded-from: <repo>:epic:<epic-id>:cited=1`, exactly
  as if no gate had been named

#### Scenario: A gate names a feature other than the next unspecced one
- **WHEN** an epic has 3 `### Feature` headings, 1 citing spec (so the next unspecced feature is
  Feature 2), and its text reads only "Feature 3 depends on Feature 2's contract" (no gate names
  Feature 2 itself)
- **THEN** a queued brief is created for Feature 2 exactly as if no gate had been named — the
  epic's own text names no precondition for the feature actually being seeded
