## Purpose

Makes epic lifecycle state (unparseable decomposition, unspecced feature gap, or fully
specced/delivered) visible as first-class dashboard rows, using the same pure-file-inspection
approach the dashboard already uses for spec rows, so a human or agent can see epic state without
opening `docs/specs/epics/*.md` files by hand.

## ADDED Requirements

### Requirement: Epic files are classified into a dashboard stage

For a file under `docs/specs/epics/` whose name matches the `NNN-slug` epic-id pattern, the
dashboard SHALL compute exactly one of three stages by pure file inspection (no git, no network):

- `epic-unparseable`: the file has no `### Feature` heading (no feature decomposition to measure
  a gap against).
- `epic-complete`: the file's `**Status:**` line matches a terminal value (e.g. Completed,
  Delivered, Superseded), OR the number of spec/change folders citing the epic id is greater than
  or equal to the number of `### Feature` headings.
- `epic-gap`: the status is not terminal and fewer spec/change folders cite the epic than there
  are `### Feature` headings.

Files under `docs/specs/epics/` that do not match the `NNN-slug` naming pattern (e.g. an index or
README) SHALL be ignored — not classified into any stage.

#### Scenario: Epic has no feature decomposition
- **WHEN** an epic file under `docs/specs/epics/` has zero `### Feature` headings
- **THEN** its computed stage is `epic-unparseable`

#### Scenario: Epic has an unspecced feature gap
- **WHEN** an epic file has 2 `### Feature` headings, a non-terminal `**Status:**` line, and only
  1 spec/change folder citing its epic id
- **THEN** its computed stage is `epic-gap`

#### Scenario: Epic's decomposition is fully specced
- **WHEN** an epic file's citing-spec count is greater than or equal to its `### Feature` heading
  count
- **THEN** its computed stage is `epic-complete`

#### Scenario: Epic's status line is terminal
- **WHEN** an epic file's `**Status:**` line matches a terminal value (e.g. Completed,
  Superseded), regardless of its citation count
- **THEN** its computed stage is `epic-complete`

#### Scenario: Non-epic file in the epics directory is ignored
- **WHEN** `docs/specs/epics/` contains a file whose name does not match the `NNN-slug` pattern
  (e.g. `README.md`)
- **THEN** no dashboard row is produced for that file

### Requirement: A repo's epics are scanned into one row per epic file

The dashboard SHALL provide a scan of a repo's `docs/specs/epics/` directory that returns one row
per matching epic file, each carrying at minimum: the epic id, its computed stage, a next-action
description, the counted feature total, the counted citing-spec total, and the list of citing
spec/change ids. A repo with no `docs/specs/epics/` directory SHALL yield zero epic rows without
error.

#### Scenario: A repo has epic files
- **WHEN** a repo's `docs/specs/epics/` directory contains 2 files matching the epic-id pattern
- **THEN** the epic scan returns 2 rows, each with an `id`, a `stage`, and a `next_action`

#### Scenario: A repo has no epics directory
- **WHEN** a repo has no `docs/specs/epics/` directory
- **THEN** the epic scan returns an empty list, and does not raise

### Requirement: Epic rows appear in dashboard JSON output

The dashboard's JSON output SHALL include the repo's epic rows, both in single-repo mode and in
multi-repo (`--repos`) mode, separately from (not merged into) the existing per-spec `specs`/
`active_specs` rows.

#### Scenario: Single-repo JSON output includes epics
- **WHEN** the dashboard is run in JSON mode for a single repo whose `docs/specs/epics/` has an
  `epic-gap` row
- **THEN** the JSON output includes that epic row, distinguishable from spec rows

#### Scenario: Multi-repo JSON output includes epics per repo
- **WHEN** the dashboard is run in JSON mode with `--repos` across repos, at least one of which
  has an epic file
- **THEN** the JSON output's per-repo entry for that repo includes its epic rows

### Requirement: Epic state renders in the human-readable dashboard

The dashboard's human-readable text output SHALL include a section summarizing outstanding
(`epic-gap` and `epic-unparseable`) epics when any exist, alongside the existing spec sections. A
repo with no outstanding epics SHALL NOT gain a new section, so existing dashboard output for
repos without an epics backlog is unchanged.

#### Scenario: Repo has an epic-gap epic
- **WHEN** the human-readable dashboard is rendered for a repo with one `epic-gap` epic
- **THEN** the output includes a line identifying that epic and its outstanding feature gap

#### Scenario: Repo has no outstanding epics
- **WHEN** a repo has no epic files, or every epic file is `epic-complete`
- **THEN** the human-readable dashboard output has no epics section

### Requirement: A malformed epic file degrades to a per-row error, not a crashed scan

If an individual epic file cannot be read or parsed, the epic scan SHALL emit an error row for
that file (stage `error`, with a diagnostic `next_action`) instead of raising and aborting the
whole scan — matching the per-spec isolation the dashboard already applies to spec folders.

#### Scenario: One epic file is unreadable
- **WHEN** `docs/specs/epics/` contains one file that matches the epic-id pattern but cannot be
  read
- **THEN** the epic scan returns an `error`-stage row for that file and still returns rows for
  every other epic file in the directory
