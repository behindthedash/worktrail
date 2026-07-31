## 1. Prompt builder

- [ ] 1.1 Add `dispatch.build_stack_conflict_prompt(spec_id, task, conflicting_branch, worktree_path)` to `src/worktrail/orchestrator/dispatch.py`, mirroring `build_group_prompt`'s `ROLE_ASSEMBLY_RESOLVE` branch (resolve conflicts minimally preserving both sides' intent, `git diff --name-only --diff-filter=U`, `git add` + `git merge --continue`, operate only in this worktree, no push/PR)
- [ ] 1.2 Unit tests for the new prompt builder in `tests/orchestrator/test_dispatch.py` (or `test_dispatch_extras.py`): renders task id, conflicting branch, and worktree path; forbids push/PR instructions (no existing PR at this stage)

## 2. Resolve-and-retry in `add_stacked_worktree`

- [ ] 2.1 Add `assembly_resolve_spawn=None` parameter to `add_stacked_worktree()` in `src/worktrail/orchestrator/live.py`
- [ ] 2.2 On sibling merge conflict, when `assembly_resolve_spawn` is provided: capture the conflicted-files list (`git diff --name-only --diff-filter=U`), build the prompt via `dispatch.build_stack_conflict_prompt`, call `assembly_resolve_spawn(prompt, wt)`, and continue instead of raising immediately
- [ ] 2.3 Add a git-state verification helper (mirroring `integrate._assembly_resolve_salvage`) that checks no `MERGE_HEAD`, clean `git status --porcelain`, and no `<<<<<<<` marker in any previously-conflicted file; only accept the resolution when it passes
- [ ] 2.4 On spawn exception, explicit-failure report-back, or failed verification: `git merge --abort` and raise `WorktreeStackConflictError` with the existing message format (unchanged from today)
- [ ] 2.5 Bound the attempt to one resolve try, reusing/mirroring `integrate.ASSEMBLY_RESOLVE_STRIKES`

## 3. Thread the seam through the two production call paths

- [ ] 3.1 In `live_run_real`, construct `assembly_resolve_spawn_fn` the same way `_pipeline_scheduler` does (`_role_agent_model(dispatch.ROLE_ASSEMBLY_RESOLVE, ...)` + `verify_module._make_live_spawn(...)`) and pass it through `ensure_wt`'s `add_stacked_worktree()` calls
- [ ] 3.2 In `full_real`'s non-pipeline `_ensure_wt`, thread the equivalent seam through its `add_stacked_worktree()` calls (mirroring the existing `assembly_resolve_spawn=` construction already used for `integrate.finish_real` at the same call site)
- [ ] 3.3 Leave `live_run`'s cassette/demo `ensure_wt` unchanged (no seam) -- confirmed non-goal in design.md

## 4. Tests

- [ ] 4.1 Extend `tests/orchestrator/test_worktree_stack_conflict.py` with: resolve spawn provided + worker succeeds + verified clean -> no raise, worktree carries both siblings' commits
- [ ] 4.2 Same file: resolve spawn provided + worker raises/exception -> `git merge --abort` called, `WorktreeStackConflictError` raised (existing message format)
- [ ] 4.3 Same file: resolve spawn provided + worker reports success but conflict markers remain / `MERGE_HEAD` still present -> treated as unverified, `WorktreeStackConflictError` raised
- [ ] 4.4 Same file: resolve spawn provided + report-back unparseable but git state is clean -> salvaged as success (mirrors `_assembly_resolve_salvage`)
- [ ] 4.5 Same file: `assembly_resolve_spawn=None` (or omitted) -> existing behavior unchanged, confirming no regression for current callers/tests
- [ ] 4.6 Confirm `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` stays green

## 5. Real-world validation (per incident lesson: unit tests alone missed this failure class)

- [ ] 5.1 Construct or reuse a real multi-sibling-conflict spec (extending the existing two-independent-siblings-editing-the-same-file scenario from `test_worktree_stack_conflict.py`'s repro) and run it through the live orchestrator path with `assembly_resolve_spawn` wired to a real resolve worker
- [ ] 5.2 Confirm the run completes unattended past the sibling conflict (no manual intervention), and that the resulting worktree/task output is correct (both siblings' changes present, task's own tests still pass)
- [ ] 5.3 Record the validation run's evidence (run record / journal reference) in the PR before merging
