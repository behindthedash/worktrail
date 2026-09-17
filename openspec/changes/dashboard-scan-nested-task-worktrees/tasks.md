## 1. Nested worktree scan (`dashboard-nested-worktree-scan`)

- [ ] 1.1 In `src/worktrail/router/dashboard.py`, make `_find_worktrees` also
      descend one level into each direct child of `<repo>-worktrees/` that is a
      directory but not a git checkout, collecting the git checkouts inside it;
      never descend into a git checkout, keep the result sorted, update the
      docstring. At both callers (`scan_repos` and the single-repo path) report
      each worktree as its POSIX path relative to `<repo>-worktrees/` instead of
      `wt.name`. (Requirements: Worktree scan includes nested task worktrees;
      Reported worktree names are relative to the worktrees directory.)
      In `tests/router/test_dashboard.py`, add real-git tests (`git worktree add`)
      covering: worktrees inside a plain container dir are returned; a
      direct-child worktree is still returned and keeps its bare name; a plain
      dir with no checkouts (e.g. `runplans/`) contributes nothing; `scan_repos`
      reports the nested one as `<container>/<name>`.
      files: src/worktrail/router/dashboard.py, tests/router/test_dashboard.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_dashboard.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate dashboard-scan-nested-task-worktrees --strict` and
      `worktrail-compile openspec/changes/dashboard-scan-nested-task-worktrees`.
      Then run the dashboard against `~/projects/gracefully-giving-back` and confirm
      the five `030-maintenance-gate-isr-spec-worktrees/maintenance-gate-isr-*`
      worktrees are listed; report them for operator teardown via
      `cleanup-worktrees` (do not remove them in this task).
