## 1. Repo resolution in fold/propose apply

- [ ] 1.1 Implement requirement: Resolve `repo` before any worktree/git op.
      In `src/worktrail/workqueue/queue_triage.py`, import
      `_resolve_repo_dir` from `..router.dashboard` and, in both
      `_apply_fold_into_change()` (line ~1571) and `_apply_propose_change()`
      (line ~1647), replace `repo_path = Path(repo)` with a call that
      resolves `repo` against a new `repos_root` parameter via
      `_resolve_repo_dir(repo, repos_root)`. When resolution returns `None`,
      return the existing error action-log shape (matching "verdict is
      missing repo or target_change/proposed_change_name") with an error
      message naming the unresolvable `repo` value, and do not call
      `_repo_base_branch`, `_fold_propose_worktree_dir`, or
      `_worktree_pr_close` (design.md Decision 3).
- [ ] 1.2 Thread `repos_root` through the call chain: add a `repos_root:
      str | Path | None = None` parameter to `apply_verdicts()`, pass it to
      both `_apply_fold_into_change()` and `_apply_propose_change()`, and
      add a `--repos-root` flag to the `apply` subparser in `main()`
      (default `None`); in `cmd_apply()`, resolve the default to
      `str(Path.home() / "projects")` when the flag is not given (design.md
      Decision 2, mirroring `dashboard.py`'s own `--repos` default) before
      passing it to `apply_verdicts()`.
- [ ] 1.3 Add regression tests in `tests/workqueue/test_queue_triage.py`
      (alongside the existing `_apply_fold_into_change`/
      `_apply_propose_change` coverage): (a) a `fold-into-change` verdict
      whose `repo` is a bare name matching a sibling directory under a
      test-supplied `repos_root` resolves and proceeds to the worktree/PR
      flow against that directory; (b) same for `propose-change`; (c) a
      `fold-into-change` (or `propose-change`) verdict whose `repo` cannot
      be resolved (no match under `repos_root`, no `repos_root` given, and
      not itself a directory) returns an `error` status action-log entry
      and does not invoke `subprocess.run`/`git`; (d) an absolute-path
      `repo` value (today's existing test shape) is unaffected by this
      change, including when `repos_root` is not passed.

## 2. Verification

- [ ] 2.1 [cleanup] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`
      and confirm it is green, including the new tests from section 1.
      Verification-only — no file changes expected.
- [ ] 2.2 [cleanup] Run `PYTHONPATH=src pytest -q` (full suite) and confirm
      it is green. Verification-only — no file changes expected.
