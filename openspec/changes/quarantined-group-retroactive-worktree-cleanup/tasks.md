## 1. Sweep classification (`quarantined-worktree-retroactive-reclaim`)

- [ ] 1.1 In `src/worktrail/router/quarantine_selfcheck.py`, add a public
      `group_task_ids(repo, spec_id, group_name) -> list[str] | None` that performs the
      cached-RunPlan lookup and `plan_groups()` match now inside `_group_files`, and
      refactor `_group_files` to call it (same `None` on missing/unreadable cache or
      unmatched group). In `src/worktrail/router/sweep_stale_worktrees.py`, build a
      per-repo attribution index in `sweep_repo()` from every `run-<spec_id>.json`
      whose group `state` is `QUARANTINED` (task worktrees `<spec_id>-<task_id>` via
      `group_task_ids`, lowercased per `worktree.py`; verify worktrees
      `<spec_id>-verify-<group>` by name; `MERGED`/`OPEN` groups never indexed), pass it
      into `classify_worktree()`, and after the existing DIRTY and unpushed-commit
      checks return `{"state": "QUARANTINE-MERGED", "reclaimable": True, "reason": ...}`
      when either `gh pr view <pr_url> --json state` reports `MERGED` (best-effort,
      degrade silently like `pr_state_for_branch`) or
      `quarantine_selfcheck.reconcile_finding()` returns a record, memoising the
      evidence lookup per group so it runs once per sweep. No evidence -> the existing
      git-only path, unchanged. Replace the module docstring's "no other mechanism ever
      comes back to it" note with the new rule; the sweep stays report-only.
      (Requirements: Journal-aware attribution of orchestrator worktrees to quarantined
      groups; Confirmed-merged quarantined group worktrees classify as reclaimable;
      Safety guards are never bypassed.)
      In `tests/router/test_quarantine_selfcheck.py` cover `group_task_ids` (match,
      unmatched group, missing cache) and that `_group_files` still returns the same
      file sets. In `tests/router/test_sweep_stale_worktrees.py`, using the existing
      real-git fixtures plus a written journal and RunPlan cache, cover: task and verify
      worktree attribution; `MERGED`/`OPEN` groups not attributed; missing RunPlan
      leaves task worktrees on the git-only path but still attributes the verify
      worktree; `QUARANTINE-MERGED` via a stubbed `MERGED` PR state on a branch
      `git cherry` still reports unmerged; `QUARANTINE-MERGED` via a stubbed
      `reconcile_finding` record with empty `pr_url`; `OPEN` PR + no reconciliation
      falls through to today's classification; a dirty attributed worktree stays
      `DIRTY`; one evidence lookup for three worktrees of one group; and the sweep
      leaves worktree, branch, and journal untouched.
      files: src/worktrail/router/quarantine_selfcheck.py, src/worktrail/router/sweep_stale_worktrees.py, tests/router/test_quarantine_selfcheck.py, tests/router/test_sweep_stale_worktrees.py

## 2. Attended cleanup procedure

- [ ] 2.1 [depends: 1.1] In `skills/worktrail-go/references/worktree-cleanup.md`, extend
      step 2 so the agent runs `worktrail-sweep-stale-worktrees --repo "$REPO" --json`
      and treats `QUARANTINE-MERGED` entries as prunable alongside MERGED / GONE, quoting
      each entry's `reason` in the step-3 table; leave the confirm -> liveness-guard ->
      remove sequence and the scoped `<spec_id>-*` filter unchanged. Update the Notes
      bullet that says a quarantined group's worktree is never auto-deleted to say it is
      kept until the sweep reports it `QUARANTINE-MERGED`. (Requirement: Attended
      cleanup consumes the journal-aware classification.)
      files: skills/worktrail-go/references/worktree-cleanup.md

## 3. Verification

- [ ] 3.1 [depends: 1.1, 2.1] [e2e] Run
      `PYTHONPATH=src pytest -q tests/router/test_sweep_stale_worktrees.py tests/router/test_quarantine_selfcheck.py tests/test_plugin_surface.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate quarantined-group-retroactive-worktree-cleanup --strict` and
      `worktrail-compile openspec/changes/quarantined-group-retroactive-worktree-cleanup`.
