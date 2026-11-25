## ADDED Requirements

### Requirement: Untrackable top-level checkboxes are detected structurally

The OpenSpec `tasks.md` parser SHALL record every top-level checkbox line that does not match
the `N.M` task syntax as an untrackable entry carrying its line number and text, in addition to
the existing warning, and SHALL expose those entries whether or not any trackable tasks parsed.
An indented checkbox is a sub-item of the task above it, not a task, and SHALL NOT be recorded
as an untrackable entry; the parser SHALL continue to tolerate it without a hard failure.

#### Scenario: A tasks.md whose checkboxes all lack ids

- **WHEN** a `tasks.md` contains `## 1. Setup` followed by `- [ ] Do the first thing` and
  `- [x] Do the second thing`, with no `N.M` ids
- **THEN** the parse yields no trackable tasks and records both lines as untrackable entries

#### Scenario: A mix of trackable and untrackable checkboxes

- **WHEN** a group contains one `- [ ] 1.1 ...` task and one `- [ ] ...` line without an id
- **THEN** the trackable task is parsed normally and only the id-less line is recorded as
  untrackable

#### Scenario: An indented checkbox is a sub-item

- **WHEN** a task line is followed by an indented `  - [ ] ...` checkbox
- **THEN** the parse succeeds and the indented line is not recorded as an untrackable task

### Requirement: Normalization assigns N.M ids under the enclosing group

The normalizer SHALL assign an `N.M` id to each untrackable top-level checkbox that appears
under a `## N.` group heading, taking `N` from that heading and the next unused integer `M`
after the highest existing id in that group (starting at 1 when the group has no ids).
Assignment SHALL follow file order within each group. Existing ids SHALL NOT be renumbered or
rewritten. Normalization SHALL preserve indentation, checkbox state (`[ ]` or `[x]`), task
text, line endings, and every byte outside the inserted id, and it SHALL be idempotent.

#### Scenario: A group with no existing ids

- **WHEN** a `## 1. Setup` group contains two untrackable checkboxes, one unchecked and one
  checked
- **THEN** they become `1.1` and `1.2` in file order, the checked box stays checked, and the
  task text is otherwise unchanged

#### Scenario: A group with existing ids

- **WHEN** a `## 2. Core` group already contains `2.1` and `2.2` and an untrackable checkbox
  appears among them
- **THEN** the untrackable line receives `2.3` and the existing ids are not changed

#### Scenario: Continuation lines stay attached

- **WHEN** an untrackable task line is followed by an indented `files:` continuation line
- **THEN** normalization inserts the id on the task line and leaves the continuation line
  byte-for-byte intact, so a re-parse attaches it to the normalized task

#### Scenario: Normalization is idempotent

- **WHEN** the normalizer runs a second time on its own output
- **THEN** the text is unchanged and no ids are assigned

### Requirement: Checkboxes outside a group are reported, never guessed

A top-level untrackable checkbox that appears before any `## N.` group heading SHALL be left
unchanged and reported as unnormalizable. The normalizer SHALL NOT invent a group number and
SHALL NOT insert a heading.

#### Scenario: A checkbox before the first heading

- **WHEN** a `tasks.md` begins with `- [ ] orphan task` before any `## N.` heading
- **THEN** the line is left unchanged, is reported as unnormalizable with its line number, and
  no group heading is added

### Requirement: A CLI applies and checks normalization

`worktrail-normalize-openspec-task-ids` SHALL accept either an OpenSpec change directory or a
`tasks.md` path. By default it SHALL rewrite the file atomically only when an id is assigned,
report each assigned id, and exit 0 when no untrackable top-level checkbox remains. `--check`
SHALL make no writes and SHALL exit non-zero when normalization is needed, and exit 0 when the
file is already normalized. In either mode, unnormalizable lines SHALL be reported and SHALL
make the exit status non-zero; the default mode still writes the ids it can assign.

#### Scenario: Default mode repairs a fixable file

- **WHEN** the CLI runs without flags on a `tasks.md` whose checkboxes are all ungrouped-eligible
  untrackable lines under a group heading
- **THEN** the file is rewritten with the assigned ids, the assignments are reported, and the
  exit status is 0

#### Scenario: Check mode reports without writing

- **WHEN** `--check` runs on a file that needs ids
- **THEN** the file is not modified and the exit status is non-zero

#### Scenario: Check mode passes on a normalized file

- **WHEN** `--check` runs on a `tasks.md` whose top-level checkboxes all carry `N.M` ids
- **THEN** the exit status is 0 and nothing is written

#### Scenario: An unnormalizable line fails the command

- **WHEN** the file contains an untrackable checkbox before any group heading alongside a
  fixable untrackable checkbox under one
- **THEN** default mode assigns the fixable id, leaves the ungrouped line unchanged, reports it,
  and exits non-zero

### Requirement: Changes with untrackable ids are not reported ready or complete

The dashboard SHALL report an OpenSpec change whose `tasks.md` contains at least one
untrackable top-level checkbox with stage `needs-tasks`, a next action naming task-id
normalization, and the untrackable count, regardless of how many trackable tasks parsed and
regardless of their status. It SHALL NOT report such a change as `create tasks`,
`ready-to-implement`, `verify-pending`, `sync-pending`, or `complete`. The row SHALL expose the
untrackable count so downstream finders can distinguish it from a genuinely task-less change.
Changes without untrackable top-level checkboxes SHALL keep their existing stage behavior.

#### Scenario: An id-less tasks.md is not "create tasks"

- **WHEN** an OpenSpec change's `tasks.md` has checkboxes but none carries an `N.M` id
- **THEN** the dashboard row's stage is `needs-tasks`, its next action names task-id
  normalization, and its untrackable count is the number of offending checkboxes

#### Scenario: A mixed change is not ready to implement

- **WHEN** an OpenSpec change has one pending `N.M` task and one untrackable top-level checkbox
- **THEN** the row's stage is `needs-tasks` and not `ready-to-implement`

#### Scenario: A completed change with an untrackable checkbox is not complete

- **WHEN** every `N.M` task in an OpenSpec change is completed but a top-level checkbox without
  an id remains
- **THEN** the row's stage is `needs-tasks` and not `complete` or `verify-pending`

#### Scenario: A change without untrackable checkboxes is unchanged

- **WHEN** an OpenSpec change's `tasks.md` is empty or every top-level checkbox carries an
  `N.M` id
- **THEN** the row keeps the stage and next action it had before this change
