## Purpose

Gives worktrail's own codex dispatch path an in-process check that refuses to launch a
codex child whose working root or additional writable directories target the canonical
(non-worktree) checkout of a git repository, so confinement to the intended worktree does
not depend solely on an external, machine-specific tool being installed.

## ADDED Requirements

### Requirement: Codex dispatch refuses a canonical-checkout working root or additional directory
When building a codex dispatch command, the system SHALL resolve the working-root target and
every additional writable directory target, and SHALL refuse to build the command (raising an
error instead of returning a command to spawn) if any of those targets is the canonical
(non-worktree) checkout root of a git repository.

A target is the canonical checkout root of a git repository when it resolves to a git working
tree whose toplevel directory is the same as that repository's common (canonical) git
directory's parent — i.e., it is a plain checkout, not a directory added via `git worktree
add`.

#### Scenario: Working root targets a canonical checkout
- **WHEN** a codex dispatch command is built with its working-root target set to the canonical
  (non-worktree) checkout root of a git repository
- **THEN** the system refuses to build the command and raises an error identifying the
  offending target and the repository

#### Scenario: Additional directory targets a canonical checkout
- **WHEN** a codex dispatch command is built with one of its additional writable directory
  targets set to the canonical (non-worktree) checkout root of a git repository
- **THEN** the system refuses to build the command and raises an error identifying the
  offending target and the repository

#### Scenario: Working root targets a linked worktree
- **WHEN** a codex dispatch command is built with its working-root target set to a directory
  created via `git worktree add` for a git repository
- **THEN** the system builds the command normally

#### Scenario: Target is not inside any git repository
- **WHEN** a codex dispatch command is built with a working-root or additional directory
  target that is not inside any git repository (or git cannot resolve it)
- **THEN** the system builds the command normally, without refusing on that target

#### Scenario: Non-codex agents are unaffected
- **WHEN** a dispatch command is built for the `claude` or `opencode` agent with any working
  directory or additional directory target
- **THEN** the system does not apply the canonical-checkout refusal to that command

### Requirement: Escape hatch overrides the canonical-checkout refusal
The system SHALL provide an explicit, opt-in override that a caller can set to allow building a
codex dispatch command whose working root or an additional directory would otherwise be
refused under the canonical-checkout check.

#### Scenario: Override is set
- **WHEN** a codex dispatch command is built with a working-root or additional directory target
  that would otherwise be refused, and the override is set
- **THEN** the system builds the command normally instead of refusing it

#### Scenario: Override is not set
- **WHEN** a codex dispatch command is built with a working-root or additional directory target
  that would otherwise be refused, and the override is not set
- **THEN** the system refuses to build the command
