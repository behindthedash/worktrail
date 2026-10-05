## Why

Codex workers launched from linked git worktrees receive the checkout and the
shared git common directory as writable roots, but not the linked worktree's
own administrative git directory. Git writes worktree-local state there, so a
worker can fail under `workspace-write` when it creates an `index.lock` or
updates other worktree metadata even though the common directory is writable.

The sandbox-confinement change already established the common-directory
workaround for an earlier `index.lock` failure. This follow-up closes the
remaining worktree-specific write surface and adds a regression test for it.

## What Changes

- Include a linked worktree's administrative git directory in the default
  writable roots emitted for Codex children, in addition to its git common
  directory.
- Preserve stable ordering and de-duplication, so a normal checkout whose
  administrative and common directories are the same does not receive a
  duplicate root.
- Extend the linked-worktree sandbox test to assert both git write locations
  are granted.

## Capabilities

### New Capabilities

### Modified Capabilities

- `codex-sandbox-confinement`: writable-root construction grants both the
  linked worktree's administrative git directory and its common git directory.

## Impact

- `src/worktrail/shared/codex_sandbox.py`: discovers and adds the
  worktree-specific git directory when available.
- `tests/shared/test_codex_sandbox.py`: covers the linked-worktree root set.
- No CLI, configuration, sandbox-mode, or external dependency change.
