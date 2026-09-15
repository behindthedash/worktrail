## ADDED Requirements

### Requirement: Minted Briefs Carry A Validated Capture Source

`create_handoff()` SHALL write a `captured-by` frontmatter key on every brief it creates, placed
immediately after `created`. The value SHALL match
`^[a-z0-9][a-z0-9-]*(:[A-Za-z0-9._/-]+)?$`.

When the caller passes no `captured_by`, the value SHALL be `unknown`. A malformed value SHALL
raise `ValueError` before any file is written.

The resulting brief SHALL still pass `validate_brief` and `is_canonical_style`.

#### Scenario: Explicit capture source is stamped

- **WHEN** `create_handoff()` is called with `captured_by="detector:stale-branch"`
- **THEN** the written brief's frontmatter has `captured-by: detector:stale-branch` on the line
  after `created`

#### Scenario: Omitted capture source defaults to unknown

- **WHEN** `create_handoff()` is called without `captured_by`
- **THEN** the written brief's frontmatter has `captured-by: unknown`

#### Scenario: Malformed capture source is rejected

- **WHEN** `create_handoff()` is called with `captured_by="Bad Source!"`
- **THEN** it raises `ValueError`
- **AND** no brief file is created in `queue/`

### Requirement: The Handoff CLI Accepts A Capture Source

The `worktrail-handoff` console script SHALL accept `--captured-by <source>` and pass it to
`create_handoff()`. When the flag is omitted, the CLI SHALL stamp `worktrail-handoff`.

#### Scenario: CLI without the flag

- **WHEN** `worktrail-handoff --focus "x" --queue-dir <tmp> --json` runs
- **THEN** the created brief has `captured-by: worktrail-handoff`

#### Scenario: CLI with the flag

- **WHEN** `worktrail-handoff --focus "x" --queue-dir <tmp> --captured-by stop-hook --json` runs
- **THEN** the created brief has `captured-by: stop-hook`

### Requirement: In-Repo Minting Callers Stamp Their Own Source

The backlog seeder SHALL create briefs with `captured-by: seed-backlog`. Cluster consolidation
SHALL write its consolidated brief with `captured-by: consolidate-cluster`.

Neither caller's `seeded-from` behavior SHALL change.

#### Scenario: Seeded brief records the seeder

- **WHEN** `worktrail-seed-backlog` creates a brief for a finding
- **THEN** the brief has `captured-by: seed-backlog`
- **AND** it still carries its `seeded-from` key

#### Scenario: Consolidated brief records consolidation

- **WHEN** cluster consolidation writes a consolidated brief
- **THEN** the brief has `captured-by: consolidate-cluster`

### Requirement: Briefs Without A Capture Source Remain Valid

A brief whose frontmatter has no `captured-by` key SHALL still pass `validate_brief`, and SHALL
keep the same `execution`/`intake` classification as before this change.

#### Scenario: Legacy brief is unaffected

- **WHEN** a pre-existing brief without `captured-by` is validated and classified
- **THEN** `validate_brief` returns `(True, None)`
- **AND** the classification matches what its `seeded-from` key alone determines
