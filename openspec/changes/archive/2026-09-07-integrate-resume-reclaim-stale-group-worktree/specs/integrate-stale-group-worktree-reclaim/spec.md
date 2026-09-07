## Purpose

Lets a resumed orchestrator run rebuild a group branch whose previous
integration attempt was killed mid-flight and left its throwaway integrate
checkout on disk, instead of aborting with `git worktree add failed`.

## ADDED Requirements

### Requirement: Leftover integrate checkout is reclaimed before group branch add

Before running `git worktree add -f -B <group_branch>` for a group, the system
SHALL find every linked worktree that has `<group_branch>` checked out and whose
path is inside the run's `<repo>-integrate/` scratch directory, and SHALL remove
each one (`git worktree remove --force`, then deleting the directory if it still
exists) under the same registry lock used for the add. Worktrees holding the
branch outside that scratch directory SHALL NOT be removed.

#### Scenario: Killed run left an integrate checkout holding the group branch

- **WHEN** a previous integration of the group was killed after `git worktree
  add` and before teardown, so `<repo>-integrate/<branch>-<id>` still exists
  with the group branch checked out
- **AND** integration of that group is attempted again
- **THEN** the leftover checkout is removed and the new `git worktree add -f
  -B` succeeds without raising `WorktreeAddError`

#### Scenario: Group branch is checked out in a worktree outside the scratch directory

- **WHEN** a worktree outside `<repo>-integrate/` has the group branch checked
  out
- **THEN** that worktree is left untouched and the existing prune-and-retry
  path runs unchanged, surfacing `WorktreeAddError` with git's stderr if the
  add still fails

#### Scenario: No leftover checkout

- **WHEN** no linked worktree has the group branch checked out
- **THEN** behavior is unchanged: a single prune followed by the add
