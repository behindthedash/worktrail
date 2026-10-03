## 1. Grant linked worktree administrative git directories

- [x] 1.1 Update `src/worktrail/shared/codex_sandbox.py` so the shared writable-root
      builder discovers a checkout's absolute administrative git directory as well as its git
      common directory, and emits the administrative directory between the child cwd and the
      common directory. Preserve the current failure handling, stable ordering, and
      de-duplication so a normal checkout emits its `.git` directory only once. In
      `tests/shared/test_codex_sandbox.py`, extend the linked-worktree regression to assert
      the emitted roots include the worktree-specific administrative directory and the common
      directory in order; add coverage that a normal checkout's shared administrative/common
      directory is not duplicated.
      (Requirement: Writable roots come from one shared helper)
      files: src/worktrail/shared/codex_sandbox.py, tests/shared/test_codex_sandbox.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q
      tests/shared/test_codex_sandbox.py`, then `PYTHONPATH=src pytest -q`. Run
      `openspec validate fix-codex-worktree-git-sandbox-roots --strict` and
      `worktrail-compile openspec/changes/fix-codex-worktree-git-sandbox-roots`.
