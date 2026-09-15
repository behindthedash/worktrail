## 1. Agent-memory isolation at the spawn choke point (`worker-agent-memory-isolation`)

- [x] 1.1 In `src/worktrail/orchestrator/spawnlib.py`, add a private helper `_ensure_agent_memory_ignored(cwd, log)` per design D2-D4 and call it once at the top of `spawn_agent`, before cell resolution; write the failing tests first in `tests/orchestrator/test_spawnlib.py`.
      Tests use a real `git init` repo plus `git worktree add` under `tmp_path`, and cover:
      a new `MEMORY.md` under each memory directory is not staged by `git add -A`;
      the `.gitignore` markers themselves are not staged;
      a modification to a tracked memory file still appears in `git status --porcelain`;
      a canonical checkout and a non-git cwd get no files;
      an existing `.gitignore` with other content is unchanged;
      an `OSError` on write is logged and the spawn still launches, with the launcher stubbed the way existing `spawn_agent` tests stub it.
      Confirm every new test fails against the current `spawnlib.py` before implementing the helper.
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py
      Covers: Worker Spawns Self-Ignore Agent Memory In Linked Worktrees; Canonical Checkouts And Non-Git Directories Are Untouched; Memory Isolation Is Idempotent And Fail-Open

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, and `openspec validate worker-agent-memory-isolation --strict`.
      Then, in a scratch repo under `$TMPDIR`, create a linked worktree and launch a real Claude probe agent with `memory: project` in it through `spawnlib.spawn_agent`.
      Run `git add -A` and confirm nothing under `.claude/agent-memory/` is staged, and that the probe's `MEMORY.md` exists on disk.
      depends: 1.1
