# dashboard-nested-worktree-scan Specification

## Purpose
Makes the dashboard's worktree scan descend into nested task worktrees and report their names relative
to the worktrees directory. Prevents in-flight orchestrator work from being invisible on the
dashboard just because it lives one level deeper than a top-level worktree.
## Requirements
### Requirement: Worktree scan includes nested task worktrees

`_find_worktrees` SHALL return every git checkout that is a direct child of
`<repo>-worktrees/`, and additionally every git checkout that is a direct child of a
non-git directory directly under `<repo>-worktrees/`. It SHALL NOT descend into a
directory that is itself a git checkout, and SHALL NOT descend more than one
container level. The result SHALL be sorted, and SHALL be empty when
`<repo>-worktrees/` does not exist.

#### Scenario: Task worktrees inside a container directory are found
- **WHEN** `<repo>-worktrees/030-spec-worktrees/` is a plain directory containing
  git worktrees `feat-1.1` and `feat-1.2`
- **THEN** `_find_worktrees` returns both `030-spec-worktrees/feat-1.1` and
  `030-spec-worktrees/feat-1.2`

#### Scenario: Direct-child worktrees are still found
- **WHEN** `<repo>-worktrees/my-branch/` is a git worktree
- **THEN** `_find_worktrees` returns it, and does not inspect its subdirectories

#### Scenario: Non-git directories without worktrees contribute nothing
- **WHEN** `<repo>-worktrees/runplans/` is a plain directory holding only files and
  non-git subdirectories
- **THEN** `_find_worktrees` returns no entry for it

### Requirement: Reported worktree names are relative to the worktrees directory

The `worktrees` list in each `scan_repos` row and in the single-repo dashboard
output SHALL name each worktree by its POSIX path relative to `<repo>-worktrees/`,
so a nested worktree is reported as `<container>/<name>` and a direct child keeps
its bare directory name.

#### Scenario: Nested worktree is named with its container
- **WHEN** `scan_repos` runs over a repo whose only worktree is
  `<repo>-worktrees/030-spec-worktrees/feat-1.1`
- **THEN** that repo's row has `worktrees == ["030-spec-worktrees/feat-1.1"]`

#### Scenario: Direct-child name is unchanged
- **WHEN** the repo's only worktree is `<repo>-worktrees/my-branch`
- **THEN** the row has `worktrees == ["my-branch"]`

