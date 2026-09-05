## 1. Exclude gitignored paths from scope escalation

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, in `_scope_escalation_files()`
      (currently building `candidates` from existing, non-absolute, in-worktree
      `missing_context` paths and then subtracting the task's own already-declared
      `files`), add a filter after that existing-file check that drops any candidate
      `git -C <worktree> check-ignore --quiet <path>` reports as ignored (return code 0),
      using the module's existing `_git()` helper with `check=False`. Apply the filter
      before the `if not candidates: return []` short-circuit, so a report whose only
      listed paths are gitignored produces no escalation at all — not a below-radar
      empty widening. In `tests/orchestrator/test_context_widening.py`, add tests calling
      `live._scope_escalation_files()` directly (following the existing direct-call style
      already in that file): (a) a `missing_context` path that exists in the worktree and
      matches a `.gitignore` pattern (e.g. write `.claude/tsc-cache/` to the test repo's
      `.gitignore` and create a file under it) returns `[]`; (b) a `missing_context` list
      mixing one gitignored path and one ordinary tracked-pattern path returns only the
      ordinary path. (Requirement: A failed review with missing-context paths triggers
      scope escalation)

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass;
      depends on 1.1.
