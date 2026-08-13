# human-decision-queue Specification

## Purpose
Lets an unattended run hand a genuine product decision to a human as a structured, answerable
record — releasing the blocked brief instead of stranding it — and lets the next unattended pass
consume the answer and continue from the blocked point, with guardrails that keep decision
filing from becoming a laziness escape hatch.
## Requirements
### Requirement: Decision records are structured and directory-arbitrated

`worktrail-decision` SHALL store records under the work-queue root at
`decisions/open/`, `decisions/answered/`, and `decisions/resolved/`, where the containing
directory — not the `status:` frontmatter field — is the arbiter of a record's state, so a
human who answers by editing the `## Answer` section and moving the file by hand is honored.
`ask` SHALL refuse a record missing a non-empty question, why-this-is-a-product-decision,
what-was-attempted context, or fewer than two options, and SHALL refuse a second open decision
for the same brief.

#### Scenario: A record is hand-moved to answered/ with a stale status field
- **WHEN** a record whose frontmatter still reads `status: open` sits in `decisions/answered/`
- **THEN** its status resolves as `answered`

#### Scenario: A lazy ask with one option
- **WHEN** `ask` is invoked with a single `--option`
- **THEN** it is refused with an error explaining at least two options are required

#### Scenario: A second open decision for the same brief
- **WHEN** `ask --brief <id>` is invoked while that brief already has an open decision
- **THEN** it is refused

### Requirement: Filing a decision blocks and releases the source brief

`ask --brief <id> --release` SHALL stamp `awaiting-decision: <decision-id>` on the brief and
release it back to `queue/`. `work_queue.list` SHALL report a brief whose linked decision is
still open as `blocked` (exposing `awaiting_decision` and `decision_status` fields), SHALL
report it unblocked once the decision is answered, and SHALL treat a decision id that resolves
to no record as unblocked so a deleted record never wedges the brief. Explicitly claiming a
brief whose decision is still open SHALL warn but not hard-block.

#### Scenario: Brief blocked while the decision is open
- **WHEN** a brief carries `awaiting-decision:` naming a record in `decisions/open/`
- **THEN** `list` reports it `blocked: true` with `decision_status: open`

#### Scenario: Brief unblocks when the human answers
- **WHEN** the linked record moves to `decisions/answered/`
- **THEN** `list` reports the brief `blocked: false` with `decision_status: answered`

#### Scenario: The decision record was deleted
- **WHEN** a brief's `awaiting-decision:` id resolves to no record in any status directory
- **THEN** `list` reports the brief unblocked

### Requirement: Answer and resolve close the loop

`answer <id> --answer <text>` SHALL write the text into the record's `## Answer` section, stamp
`answered-at`, and move it to `answered/`. `resolve <id>` SHALL move an answered record to
`resolved/`, stamp `resolved-at`, and strip the linked brief's `awaiting-decision` field;
resolving a still-open record SHALL be refused so a pending question is never silently
discarded.

#### Scenario: Resolving an open decision
- **WHEN** `resolve` is invoked on a record still in `decisions/open/`
- **THEN** it is refused with a `still-open` result

#### Scenario: Resolving an answered decision with a linked brief
- **WHEN** `resolve` is invoked on an answered record whose brief still exists
- **THEN** the record moves to `decisions/resolved/` and the brief's `awaiting-decision`
  frontmatter field is removed

### Requirement: The drain rewards filed decisions and punishes decision-less blocks

`worktrail-drain` SHALL detect whether a `blocked_product_decision` iteration filed at least one
new open decision during that iteration. A decision-filed block SHALL NOT count toward the
consecutive-failure circuit breaker and SHALL be logged with the filed ids; a decision-less
block SHALL count exactly as before. The run summary SHALL carry each iteration's
`decisions_filed` list and a `decisions_open` total, and the end-of-run log SHALL tell the
operator how to review and answer open decisions.

#### Scenario: Repeated blocks that each filed a decision
- **WHEN** consecutive iterations finish `blocked_product_decision` and each filed a new open
  decision
- **THEN** the circuit breaker does not trip and the drain continues to the next ready brief

#### Scenario: Repeated blocks that filed nothing
- **WHEN** consecutive iterations finish `blocked_product_decision` without filing any decision
- **THEN** the circuit breaker trips at its configured threshold exactly as before

