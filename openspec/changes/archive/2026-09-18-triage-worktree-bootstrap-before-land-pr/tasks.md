## 1. Bootstrap the triage worktree before landing

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, in `_worktree_pr_close`: after the
      `git worktree add` success check and before `prepare(worktree_dir)` (inside the
      existing `try` so the `finally` cleanup applies), read
      `policy_mod.load_policy(repo_path).get("worktree_bootstrap_cmd")` (local import of
      `..router.policy` as the file already does elsewhere) and call
      `orchestrator.live.bootstrap_worktree(worktree_dir, cmd, log=..., required=True)`
      with a logger that writes to `sys.stderr`. Catch `WorktreeAddError` and return the
      existing `{**result, "status": "error", "path": None, "error": f"worktree bootstrap
      failed: {e}", "branch": branch}` shape so the brief is released and `land_pr` is
      never reached. An unset/empty command must not run any subprocess. Add the bootstrap
      step to the docstring's sequence description.
      (Requirement: Fold and propose worktrees are bootstrapped before landing.)
      In `tests/workqueue/test_queue_triage.py`, in the propose-change apply test class
      that already mocks `queue_triage.subprocess.run` and `queue_triage.land_pr`, add
      three tests: with a `.worktrail/policy.yaml` in the resolved repo setting
      `worktree_bootstrap_cmd: npm ci`, assert `bootstrap_worktree` (patched at
      `worktrail.workqueue.queue_triage.bootstrap_worktree` or via the shell call
      captured by the dispatcher) runs with `cwd` equal to the worktree dir, after the
      `worktree add` call and before `land_pr`, and the entry is `executed`; with no
      `worktree_bootstrap_cmd`, assert no shell bootstrap call is seen and the entry is
      `executed`; with the bootstrap raising `WorktreeAddError` (or the dispatcher
      returning rc 1 for it), assert the entry is `status: error` naming the bootstrap,
      `land_pr` was not called, the brief file is back under `queue/`, and a
      `worktree remove` call was issued.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate triage-worktree-bootstrap-before-land-pr --strict` and
      `worktrail-compile openspec/changes/triage-worktree-bootstrap-before-land-pr`.
