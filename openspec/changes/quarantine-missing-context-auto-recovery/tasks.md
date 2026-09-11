## 1. Detection and recovery (`missing-context-auto-recovery`)

- [x] 1.1 Add `_missing_context_recovery(task, report, wt, by_id, repo, remote, base)` to
      `src/worktrail/orchestrator/live.py` beside `_scope_escalation_files`: validate paths per
      design D1 (repo-relative, declared by another task in `by_id`, absent from the worktree,
      non-empty blob on `_live_base_ref`), return the qualifying paths and sibling ids, and honor
      the once-per-task guard. Wire it into both `drive()` report sites that call `_commit_step`
      ahead of scope escalation and adaptive read-widening; on a hit, journal the
      `missing_context_auto_recovery` event with `category: orchestrator_defect`, record the
      triggering entry with `auto_recovered: true` and no terminal status, remove the worktree
      and branch under the git lock, reset the task to `pending` with strikes and
      scope/extra-read state cleared, and print the operator line. Extend journal replay so the
      event restores `pending` plus the guard on resume. Add
      `tests/orchestrator/test_missing_context_auto_recovery.py` with a real git fixture (sibling
      merged to base after the task's fork) covering the positive case, each negative case in the
      spec, once-only, worktree/branch removal, event contents, replay, and `clear_tasks()`
      finding nothing terminal.
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_missing_context_auto_recovery.py
      Covers: A failed report naming a merged sibling file triggers auto-recovery; Recovery re-dispatches from a fresh worktree without a human; Recovery is journaled as an auditable intervention

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run the new test module, `tests/orchestrator/test_context_widening.py`,
      `tests/orchestrator/test_live_manual_recovery.py`, and
      `tests/orchestrator/test_quarantine_write_sites_structural.py`, then `pytest -q` and
      `python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate quarantine-missing-context-auto-recovery --strict` and
      `worktrail-compile openspec/changes/quarantine-missing-context-auto-recovery`.
