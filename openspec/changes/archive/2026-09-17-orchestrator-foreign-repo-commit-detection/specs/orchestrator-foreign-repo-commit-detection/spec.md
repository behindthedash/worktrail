## ADDED Requirements

### Requirement: Empty-diff quarantine distinguishes foreign-repo targets
When a group's merged deliverable branches produce no diff against the group's target, the
orchestrator SHALL examine the declared file scope of the group's deliverable tasks. A
declared path is foreign when it is absolute or home-relative (`~`) or climbs out via `..`,
and resolves to a location outside the repository root. When at least one declared path is
foreign, the group SHALL be quarantined with the reason `foreign_repo_target` rather than
`empty_diff`, and the quarantine message SHALL name the foreign repositories (the git
top-level of each foreign path, or the path itself when it is not inside a git repository).
When no declared path is foreign, the group SHALL be quarantined with `empty_diff` exactly
as before.

#### Scenario: Group targeting a sibling repo
- **WHEN** a group's merge yields no diff and one of its deliverable tasks declares
  `../note-forth/.github/workflows/ci.yml`, which resolves inside a sibling git repository
- **THEN** the group is journaled `QUARANTINED` with reason `foreign_repo_target` and the
  message names the `note-forth` repository

#### Scenario: True no-op stays empty_diff
- **WHEN** a group's merge yields no diff and every declared path of its deliverable tasks
  resolves inside the repository
- **THEN** the group is journaled `QUARANTINED` with reason `empty_diff`

#### Scenario: Tasks with no declared scope
- **WHEN** a group's merge yields no diff and its deliverable tasks declare no files
- **THEN** the group is journaled `QUARANTINED` with reason `empty_diff`

### Requirement: Unmanaged default-branch commits in a foreign repo are reported
For each foreign git repository identified for a `foreign_repo_target` quarantine, the
orchestrator SHALL read-only inspect it and, when its checked-out branch is the repository's
default branch (its `origin/HEAD` branch, else `main` or `master`) and that branch has
commits not present on its upstream, SHALL include in the quarantine message the repository,
the branch, and the count of unpushed commits, flagged as an unmanaged default-branch
commit. The orchestrator SHALL NOT modify, reset, or push the foreign repository, and a
failure to inspect it SHALL NOT change the quarantine reason or abort integration.

#### Scenario: Worker committed onto a sibling repo's local main
- **WHEN** a foreign repository is on `main`, its default branch, with 2 commits ahead of
  `origin/main`
- **THEN** the quarantine message reports that repository, `main`, and 2 unpushed commits as
  an unmanaged default-branch commit

#### Scenario: Foreign repo is clean
- **WHEN** a foreign repository's default branch has no commits ahead of its upstream
- **THEN** the group is still quarantined `foreign_repo_target` and no unmanaged-commit
  warning is included for that repository

#### Scenario: Foreign repo cannot be inspected
- **WHEN** a git command against a foreign repository fails
- **THEN** the group is still quarantined `foreign_repo_target`, no warning is emitted for
  that repository, and the foreign repository is left untouched
