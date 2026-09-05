## MODIFIED Requirements

### Requirement: Stale-bookkeeping remediation
The system SHALL detect specs in the `stale-bookkeeping` stage across
`--repos-root` — devkit specs and OpenSpec changes alike — and, for each,
close out the affected stale task(s) using the mechanism native to the
finding's format and open a docs-only pull request, without invoking the
orchestrator. The finder SHALL carry each finding's format (`devkit` when the
dashboard row declares none, otherwise the row's `format`, e.g. `openspec`)
so the action can branch on it. The action SHALL NOT look for devkit
`TASK-*.md` files for an OpenSpec finding.

#### Scenario: A spec has one or more stale-bookkeeping tasks
- **WHEN** `detect_stage()` reports a devkit spec's stage as
  `stale-bookkeeping` with one or more stale task ids
- **THEN** the sweep flips each stale task's `status:` frontmatter field to
  `completed` via the existing devkit task-status-completion mechanism,
  commits the change on a short-lived branch, and opens a pull request
  carrying the `go:risk-low` label

#### Scenario: An OpenSpec change has one or more stale-bookkeeping tasks
- **WHEN** the common dashboard scan reports an OpenSpec change
  (`format == "openspec"`) as `stale-bookkeeping` with one or more stale
  task ids
- **THEN** the sweep, in the same short-lived fix-branch worktree, flips
  each stale task's checkbox in the change's `tasks.md`, runs
  `openspec archive -y <change-id>`, commits the resulting flip and archive
  move, and opens a docs-only pull request carrying the `go:risk-low` label
  whose body states the change was flipped and archived

#### Scenario: OpenSpec flip-and-archive reports an error
- **WHEN** the OpenSpec flip-and-archive step reports an error (e.g.
  `tasks.md` missing, unknown task ids with nothing to flip, or
  `openspec archive` failing)
- **THEN** the action raises and no commit, push, or pull request is
  attempted for that finding, leaving it to the sweep's per-finding failure
  isolation

#### Scenario: No stale-bookkeeping specs found
- **WHEN** no repo under `--repos-root` currently reports the
  `stale-bookkeeping` stage
- **THEN** the sweep performs no git or PR operations for this remediation
  category and the summary's `resumed_stale_bookkeeping` key is an empty list
