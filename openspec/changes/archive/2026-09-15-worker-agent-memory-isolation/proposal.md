## Why

This is Epic 003 Feature 1 (`docs/specs/epics/003-agent-learning-from-run-outcomes.md`).

Claude Code agents can declare `memory: project`. A spike on 2026-09-12 (Claude Code 2.1.270)
checked what happens when a worker session uses such an agent, either directly with `--agent` or
by delegating through the Agent tool. The agent writes `.claude/agent-memory/<agent>/MEMORY.md`
at the worktree root, and the file is untracked. In the spike, `git status` in the linked
worktree listed `?? .claude/agent-memory/`.

Orchestrator paths stage with `git add -A`:
- `integrate.py` runs it after `openspec archive`;
- `spawnlib.prepare_opencode_child_environment` notes that workers run it.

Once a repo defines a memory-enabled agent, that memory file therefore lands in the PR. Parallel
workers commit conflicting copies, and worktree teardown throws the knowledge away anyway.

Worktrail already self-ignores its own `.worktrail/` scratch directory for this exact reason, but
only in the OpenCode child-environment path, and it never covers agent memory.

## What Changes

- Add a private spawnlib helper that runs when a spawn's cwd is inside a **linked** git worktree.
  It writes a `.gitignore` containing `*` into `.claude/agent-memory/` and
  `.claude/agent-memory-local/` at that worktree's top level, only when no `.gitignore` exists
  there.
- `spawn_agent` calls the helper once before launching a worker on any harness.
- A canonical checkout (main working tree) or a non-git cwd gets no files.
- The step fails open: a failure is written to the spawn's `log` callback and the spawn proceeds.
- Memory files the repo already tracks keep normal git behavior.

## Capabilities

### New Capabilities

- `worker-agent-memory-isolation`: agent memory written during a worker session in a linked
  worktree is ignored by git. It never reaches a worktree commit, and canonical checkouts are
  never touched.

### Modified Capabilities

_None._

## Impact

- `src/worktrail/orchestrator/spawnlib.py`: the new helper, plus one call in `spawn_agent`.
- `tests/orchestrator/test_spawnlib.py`: real-git tests using `git init` and `git worktree add`
  under `tmp_path`.
- No new CLI, flag, policy key, or dependency.
- Task-worktree salvage (`task-worktree-uncommitted-work-salvage`) is unaffected. Ignored files
  are not uncommitted work, and task work outside these two directories is not ignored.
