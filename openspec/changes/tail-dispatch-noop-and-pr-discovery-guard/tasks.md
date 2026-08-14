## 1. Zero-file tail-task no-op dispatch (`tail-task-noop-dispatch`)

- [ ] 1.1 In `src/worktrail/orchestrator/dispatch.py:build_worker_prompt`,
      after the existing `scope = ", ".join(task.get("files", [])) or "(see
      task file)"` line, compute `is_noop_tail = task.get("kind") in ("e2e",
      "cleanup") and not task.get("files")` and, when true, use an explicit
      no-op/verification-only scope string in place of the current fallback
      for the `f"Scope (only touch these): {scope}"` render line (design.md
      Decision 1). Leave the fallback unchanged for every other case
      (non-tail tasks with empty `files`, tail tasks with a non-empty
      `files` list).
- [ ] 1.2 Add a regression test (alongside the existing `ROLE_CLEANUP`
      coverage in `tests/orchestrator/test_dependency_fixes.py`, or
      `tests/orchestrator/test_dispatch_extras.py`) asserting: (a) a task
      with `kind: e2e`/`kind: cleanup` and empty `files: []` renders a
      prompt that does NOT contain the bare `"(see task file)"` string and
      DOES state the task is verification-only with zero expected file
      changes; (b) the same `kind` with a non-empty `files` list renders
      the files list unchanged; (c) a non-tail `kind` with empty `files`
      still renders the pre-existing `"(see task file)"` fallback.
- [ ] 1.3 Add a regression test against
      `integrate.detect_unreconciled_tail_evidence` (in
      `tests/orchestrator/test_live_tail_reconciliation.py` or
      `tests/orchestrator/test_integrate_complete.py`, alongside its
      existing coverage) pinning the already-implied contract explicitly:
      a terminal tail task whose worktree HEAD never advanced past its
      stacked base (genuinely zero commits) produces no finding — i.e. no
      reconciliation PR is opened for it (design.md Decision 2; this locks
      in existing behavior, no production code changes here).

## 2. Operator-PR-discovery branch guard (`operator-pr-discovery-branch-guard`)

- [ ] 2.1 In `src/worktrail/orchestrator/integrate.py:integrate_one`,
      extend the `gh pr list --search` call's `--json` fields from
      `number,state,url,headRefName,isDraft` to
      `number,state,url,headRefName,baseRefName,isDraft` (design.md
      Decision 3).
- [ ] 2.2 After parsing `matches = json.loads(search_result.stdout)`, filter
      to candidates whose `headRefName == gb` or `baseRefName == pr_base`
      before taking `matches[0]`; when no candidate survives the filter,
      fall through to the existing `gh pr create` path exactly as if
      `matches` were empty.
- [ ] 2.3 Add a regression test in `tests/orchestrator/test_integrate.py`
      (or `test_integrate_extras.py`, alongside the existing operator-PR
      search coverage) asserting: (a) a search result whose sole match has
      a `headRefName` equal to the group's branch (or `baseRefName` equal
      to the group's target) is still accepted, unchanged from current
      behavior; (b) a search result whose match's `headRefName` and
      `baseRefName` correspond to neither the group's branch nor its
      target (the incident's unrelated-PR case) is rejected and the flow
      falls through to `gh pr create`.

## 3. Verification

- [ ] 3.1 Run `PYTHONPATH=src pytest -q` and confirm it is green, including
      the new tests from sections 1 and 2.
- [ ] 3.2 Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate
      check` (golden record/replay regression) and confirm it is green.
