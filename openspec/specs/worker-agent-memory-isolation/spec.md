# worker-agent-memory-isolation Specification

## Purpose
Makes worker spawns self-ignore agent memory inside linked worktrees, idempotently and fail-open,
leaving canonical checkouts and non-git directories untouched. Prevents one task worker's memory
writes from leaking into another worker's context.
## Requirements
### Requirement: Worker Spawns Self-Ignore Agent Memory In Linked Worktrees

Before `spawn_agent` launches a worker on any harness, the system SHALL check the worker's cwd.
When it is inside a linked git worktree, each of `.claude/agent-memory/` and
`.claude/agent-memory-local/` at that worktree's top level SHALL contain a `.gitignore` whose
content is `*`. Files an agent creates under those directories are then ignored by git and never
staged by `git add -A`.

#### Scenario: New project-scope memory file in a task worktree is not staged

- **WHEN** a worker is spawned with its cwd in a linked worktree
- **AND** a file `.claude/agent-memory/<agent>/MEMORY.md` is then created in that worktree
- **AND** `git add -A` runs in that worktree
- **THEN** no path under `.claude/agent-memory/` is staged
- **AND** `git status --porcelain` lists no path under `.claude/agent-memory/`

#### Scenario: New local-scope memory file in a task worktree is not staged

- **WHEN** a worker is spawned with its cwd in a linked worktree
- **AND** a file `.claude/agent-memory-local/<agent>/MEMORY.md` is then created
- **AND** `git add -A` runs in that worktree
- **THEN** no path under `.claude/agent-memory-local/` is staged

#### Scenario: Tracked memory file keeps normal git behavior

- **WHEN** a linked worktree already tracks `.claude/agent-memory/<agent>/MEMORY.md`
- **AND** a worker is spawned there and the tracked file is then modified
- **THEN** `git status --porcelain` still reports the modification

### Requirement: Canonical Checkouts And Non-Git Directories Are Untouched

The system SHALL NOT create any file or directory for agent-memory isolation when the spawn cwd
is in a repository's main working tree or is not inside a git work tree.

#### Scenario: Spawn in a canonical checkout writes nothing

- **WHEN** `spawn_agent` is called with its cwd in a repository's main working tree
- **THEN** neither `.claude/agent-memory/.gitignore` nor `.claude/agent-memory-local/.gitignore`
  is created in that checkout

#### Scenario: Spawn in a non-git directory writes nothing

- **WHEN** `spawn_agent` is called with its cwd outside any git work tree
- **THEN** no `.claude/agent-memory/` or `.claude/agent-memory-local/` path is created

### Requirement: Memory Isolation Is Idempotent And Fail-Open

The system SHALL leave any existing `.gitignore` in either memory directory byte-for-byte
unchanged. The isolation step SHALL NOT raise. When the step fails, the system SHALL report the
failure through the spawn's `log` callback and SHALL still launch the worker.

#### Scenario: Existing .gitignore is preserved

- **WHEN** a linked worktree already has `.claude/agent-memory/.gitignore` with content other than
  `*`
- **AND** a worker is spawned there
- **THEN** that file's content is unchanged

#### Scenario: Isolation failure does not block the spawn

- **WHEN** writing the `.gitignore` fails with an `OSError` during `spawn_agent`
- **THEN** the log callback receives a line naming the agent-memory isolation failure
- **AND** the worker launch still proceeds

