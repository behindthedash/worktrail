# Tasks

## 1. Use the push remote for the triage base ref

- [ ] 1.1 In `src/worktrail/workqueue/queue_triage.py`, make `_worktree_pr_close()` call
      `_push_target(repo_path)` once (right after the successful claim) and bind the returned
      remote name; use it in place of the `"origin"` literal for the `git fetch <remote>
      <base_branch>` call and its failure error text, pass it to `_unpushed_base_error()`,
      and use `f"{remote}/{base_branch}"` as the `git worktree add -b ... ` base ref. Change
      `_unpushed_base_error(repo_path, base_branch)` to
      `_unpushed_base_error(repo_path, base_branch, remote)` so the rev-list range is
      `f"{remote}/{base_branch}..{base_branch}"` and the returned error text names
      `<remote>/<base_branch>` in both places it currently says `origin/<base_branch>`.
      Update both docstrings (and the `_worktree_pr_close()` docstring's
      "fetches `origin/<base_branch>`" wording) to describe the resolved push remote.
      `_push_target()` itself is unchanged. Extend `tests/workqueue/test_queue_triage.py`:
      with a fake runner that answers `git config --get remote.pushDefault` with `fork`
      (and `git remote get-url fork` with a GitHub URL), the fold and propose apply paths
      issue `fetch fork <base>`, `rev-list --count fork/<base>..<base>`, and a
      `worktree add` whose last argument is `fork/<base>`, and no git call names
      `origin/<base>`; with `remote.pushDefault=fork` and a rev-list count of 2 the apply
      returns an error containing `ahead of fork/main` and releases the brief; the existing
      `origin` assertions (fetch/add/rev-list/error-text tests) still pass unchanged when
      `remote.pushDefault` is unset.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
      files: src/worktrail/workqueue/queue_triage.py tests/workqueue/test_queue_triage.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`, then
      `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`,
      and confirm all pass.
      depends: 1.1
