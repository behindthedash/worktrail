## 1. Repo resolution in fold/propose apply

- [x] 1.1 Implement requirement: Resolve `repo` before any worktree/git op.
      In `src/worktrail/workqueue/queue_triage.py`, import
      `_resolve_repo_dir` from `..router.dashboard` and, in both
      `_apply_fold_into_change()` and `_apply_propose_change()`, replace
      `repo_path = Path(repo)` with `_resolve_repo_dir(repo, repos_root)`
      where `repos_root` is a new `str | Path | None = None` parameter on
      both functions and on `apply_verdicts()` (threaded through). When
      resolution returns `None`, return the existing error action-log shape
      with a message naming the unresolvable `repo` value, and do not call
      `_repo_base_branch`, `_fold_propose_worktree_dir`, or
      `_worktree_pr_close` (design.md Decisions 1-3). Add a `--repos-root`
      flag to the `apply` subparser in `main()` (default `None`); in
      `cmd_apply()`, resolve the default to `str(Path.home() / "projects")`
      when the flag is not given (Decision 2) and pass it to
      `apply_verdicts(repos_root=...)`. Add regression tests in
      `tests/workqueue/test_queue_triage.py`: (a) a bare-name `repo`
      matching a sibling directory under a test-supplied `repos_root`
      proceeds to the worktree/PR flow against that directory for both
      fold and propose; (b) an unresolvable `repo` returns an `error`
      action-log entry and never invokes `subprocess.run`/`git`; (c) an
      absolute-path `repo` is unaffected, including with no `repos_root`;
      (d) `cmd_apply` forwards an explicit `--repos-root` and defaults to
      `~/projects` otherwise.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Repo-less propose-change

- [x] 2.1 Let the `__none__` group propose into a known repo and stamp it on
      the brief (design.md Decisions 4-5). In
      `src/worktrail/workqueue/queue_triage.py`: give `evaluate_group()` a
      `repos_root: str | Path | None = None` parameter and, when `repo ==
      NO_REPO_KEY`, list the directory basenames under it as `{known_repos}`
      in `EVALUATOR_PROMPT_TEMPLATE`; reword the prompt's no-repo rule so
      `propose-change` is allowed when the evidence names one of those repos
      as `target_repo` (fold stays invalid, `needs-decision` otherwise);
      thread `known_repos` through `parse_verdicts()` (a
      `known_repos_by_brief`-style argument alongside `candidates_by_brief`)
      into `_has_valid_target()` so a repo-less `propose-change` is valid only
      when `target_repo` is in that list; have `cmd_evaluate` pass the same
      `--repos-root` flag `apply` takes. Add a small helper returning a
      verdict's effective repo (group `repo` when not `NO_REPO_KEY`, else
      `target_repo`) and use it in `_apply_propose_change()`,
      `_propose_change_over_cap()`, and `_preview_verdict()`. In
      `_apply_propose_change()`, after `_resolve_repo_dir()` succeeds and
      before `_worktree_pr_close()`, when the group repo was `NO_REPO_KEY`
      call `work_queue._set_fm_fields(brief_path, {"repo": target_repo})`
      on the queued brief; report the stamp in the action-log entry and, in
      preview mode, as `planned_stamp: {"repo": ...}`. Add tests in
      `tests/workqueue/test_queue_triage.py`: prompt lists known repos for
      the no-repo group and states the rule; a repo-less `propose-change`
      naming a listed repo parses as-is, naming an unlisted repo downgrades
      to `keep`, and a repo-less `fold-into-change` downgrades to `keep`;
      repo-bearing groups are unchanged; a repo-less propose-change stamps
      `repo:` before the worktree op and the stamp survives a downstream PR
      failure; an unresolvable `target_repo` stamps nothing and returns the
      error entry; preview reports the planned stamp; the WIP-cap check reads
      the effective repo.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 3. Verification

- [x] 3.1 [cleanup] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`
      and confirm it is green, including the new tests from sections 1-2.
      Verification-only — no file changes expected.
- [x] 3.2 [cleanup] Run `PYTHONPATH=src pytest -q` (full suite) and confirm
      it is green. Verification-only — no file changes expected.
