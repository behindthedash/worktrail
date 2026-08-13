## MODIFIED Requirements

### Requirement: Decision records are structured and directory-arbitrated

`worktrail-decision` SHALL store records under the work-queue root at
`decisions/open/`, `decisions/answered/`, and `decisions/resolved/`, where the containing
directory — not the `status:` frontmatter field — is the arbiter of a record's state, so a
human who answers by editing the `## Answer` section and moving the file by hand is honored.
`ask` SHALL refuse a record missing a non-empty question, plain-English background,
why-this-is-a-product-decision, what-was-attempted context, or fewer than two options, and
SHALL refuse a second open decision for the same brief. The rendered record SHALL carry the
background as its own section, SHALL present options in the agent's priority order with a note
that the human may answer by number or write their own direction, and — when per-option costs
are supplied — SHALL label each option with its cost. Per-option costs SHALL be index-matched
to options: supplying a different count than the number of options SHALL be refused.

#### Scenario: A record is hand-moved to answered/ with a stale status field
- **WHEN** a record whose frontmatter still reads `status: open` sits in `decisions/answered/`
- **THEN** its status resolves as `answered`

#### Scenario: A lazy ask with one option
- **WHEN** `ask` is invoked with a single `--option`
- **THEN** it is refused with an error explaining at least two options are required

#### Scenario: A second open decision for the same brief
- **WHEN** `ask --brief <id>` is invoked while that brief already has an open decision
- **THEN** it is refused

#### Scenario: An ask without a plain-English background
- **WHEN** `ask` is invoked with a missing or empty `--background`
- **THEN** it is refused

#### Scenario: Options labeled with costs
- **WHEN** `ask` is invoked with two options and two `--option-cost` values
- **THEN** the rendered record lists each option in the given priority order with its cost
  beneath it

#### Scenario: Cost count does not match option count
- **WHEN** `ask` is invoked with two options and one `--option-cost`
- **THEN** it is refused with an error naming the mismatch
