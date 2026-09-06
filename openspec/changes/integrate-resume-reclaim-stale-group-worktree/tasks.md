## 1. Reclaim leftover integrate checkouts (`integrate-stale-group-worktree-reclaim`)

- [ ] 1.1 Implement requirement: Leftover integrate checkout is reclaimed before group branch add.
      In `src/worktrail/orchestrator/integrate.py:_integration_worktree`,
      inside the `with lock:` block and before the first `worktree prune`,
      call `live._worktree_checkouts_on_branch(repo, branch)` and, for each
      returned path located under `repo.parent / f"{repo.name}-integrate"`,
      run `git worktree remove --force <path>` (check=False) followed by
      `shutil.rmtree(path, ignore_errors=True)`. Paths outside that
      directory are skipped. Leave the existing prune/add/retry sequence and
      the `WorktreeAddError` message unchanged. In the same task add
      regression tests in `tests/orchestrator/test_integrate.py` next to the
      existing `_integration_worktree` tests: (a) create a real worktree
      under `<repo>-integrate/` with the group branch checked out, then enter
      `_integration_worktree` for the same branch and assert it yields a
      fresh path, the leftover directory is gone, and no `WorktreeAddError`
      is raised; (b) create a worktree with the group branch checked out
      outside `<repo>-integrate/`, assert it still exists afterwards and
      that `WorktreeAddError` is raised.

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator` and
      `PYTHONPATH=src pytest -q tests/orchestrator/lifecycle/test_lifecycle_harness.py -k KillAndResume`
      and confirm both are green. Verification-only — no file changes expected.
