## MODIFIED Requirements

### Requirement: OpenSpec change archive remediation

The system SHALL detect OpenSpec changes reported at the `complete` stage by
the common dashboard scan (`format == "openspec"`, `stage == "complete"` —
already the signal for "all tasks completed, code merged, delta reconciled
into `openspec/specs/`") across `--repos-root` and, for each, run
`openspec archive -y <change-id>` in a short-lived worktree, commit the
resulting archive move, and open a docs-only pull request carrying the
`go:risk-low` label.

The finder SHALL restrict matches to `format == "openspec"`. A devkit-format
spec reported at `stage == "complete"` carries a different meaning ("open PR
/ sync — verify merge state", not "ready to archive") and SHALL NOT be
selected by this remediation.

Before invoking `openspec archive` for a finding, the system SHALL parse the
change's `tasks.md` and refuse — raising, with no `openspec archive`,
commit, push, or pull-request attempted — if any task's status is not
completed. This check is independent of the dashboard scan's `complete`
stage determination: the `openspec` CLI itself only downgrades an
incomplete-tasks failure to a stdout warning and proceeds when `-y` is
passed, so the sweep SHALL NOT rely solely on upstream stage detection
before archiving.

#### Scenario: An OpenSpec change is at the complete stage
- **WHEN** the common dashboard scan reports an OpenSpec change as
  `stage == "complete"`
- **THEN** the sweep runs `openspec archive -y <change-id>` in a short-lived
  worktree off the repo's base branch, commits the archive move, pushes the
  branch, and opens a docs-only pull request labeled `go:risk-low`

#### Scenario: No complete OpenSpec changes found
- **WHEN** no repo under `--repos-root` currently reports an OpenSpec change
  at `stage == "complete"`
- **THEN** the sweep performs no archive operation and the summary's
  `resumed_openspec_archive` key is an empty list

#### Scenario: A devkit spec at the complete stage is not archived
- **WHEN** the common dashboard scan reports a devkit-format spec as
  `stage == "complete"`
- **THEN** the archive remediation's finder does not select that spec

#### Scenario: Already-open archive PR is not re-attempted
- **WHEN** a prior sweep already opened an archive pull request for a
  change's archive branch and it has not yet merged
- **THEN** the next sweep detects the existing open PR and returns it as-is
  rather than re-running `openspec archive`

#### Scenario: A finding's tasks.md still has an unchecked task despite complete-stage detection
- **WHEN** a finding selected for archive has a `tasks.md` in which at least
  one task's status is not completed, even though the dashboard scan
  reported the change as `stage == "complete"`
- **THEN** the sweep refuses and raises before invoking `openspec archive`,
  and no commit, push, or pull request is attempted for that finding
