## 1. Resolve the PR target remote in the preflight helper

- [ ] 1.1 In `src/worktrail/router/automerge_preflight.py`, give `owner_repo_from_git` a
      `remote: str | None = None` parameter. When `remote` is None, run
      `git config --get remote.pushDefault` (via `runner`, `cwd=repo`) and use its stripped
      stdout when the call succeeds and is non-empty, else `origin`. Then run
      `git remote get-url <selected>` and parse as today. Update the docstring (no longer
      "the origin remote") and change `required_checks_gate`'s unresolved-remote reason to
      name the selected remote, e.g. `could not resolve a GitHub owner/repo from the git
      '<remote>' remote`. Keep the failure non-query-error (no `gh api failed` marker).
      (Requirements: Preflight resolves the PR target remote.)
      In `tests/router/test_automerge_preflight.py`, extend `_FakeRunner`/`_RoutedRunner`
      to script `git config` and per-remote `git remote get-url` responses, and add tests for
      the four spec scenarios: fork layout returns the fork slug and the gate's `gh api`
      calls embed `me/proj`; unset pushDefault still returns `acme/widgets`; explicit
      `remote=` bypasses pushDefault; pushDefault naming a missing remote returns None and
      the gate reason names `fork` with `is_preflight_query_error` False.
      files: src/worktrail/router/automerge_preflight.py, tests/router/test_automerge_preflight.py

- [ ] 1.2 In `src/worktrail/orchestrator/verify.py`, change `_preflight_runner` so any
      command whose first token is `git` is rewritten to `["git", "-C", str(self.repo),
      *cmd[1:]]` (the current check only matches `git remote get-url`). Update the docstring.
      (Requirement: Orchestrator preflight adapter covers every git call.)
      In `tests/orchestrator/test_verify.py`, next to the existing `_preflight_runner` tests
      (~line 3091), add a test that `["git", "config", "--get", "remote.pushDefault"]` is
      passed through with `-C <repo>` inserted, and that `gh api` commands are still untouched.
      files: src/worktrail/orchestrator/verify.py, tests/orchestrator/test_verify.py

## 2. Verification

- [ ] 2.1 [depends: 1.1, 1.2] [e2e] Run `PYTHONPATH=src pytest -q
      tests/router/test_automerge_preflight.py tests/orchestrator/test_verify.py
      tests/router/test_check_review_threads.py tests/router/test_pr_labels.py`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate automerge-preflight-resolve-pr-target-remote --strict` and
      `worktrail-compile openspec/changes/automerge-preflight-resolve-pr-target-remote`.
