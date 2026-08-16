## 1. Worktree-to-run-record lookup

- [ ] 1.1 Implement the `find-by-worktree` subcommand in `run_record.py`: given `--dir`,
      `--repo`, `--worktree`, scan `<dir>/<repo-name>/*.yaml` with `_load_lenient`
      (skip and warn on malformed files), filter to non-terminal records whose
      `worktree` field equals the target path, and print
      `{"found": bool, "path": str|null, "run_id": str|null}` — the most recently
      started match when more than one candidate matches. Add a docstring entry in the
      module's CLI reference following the `active-conflicts`/`liveness` style.
      (Requirement: Worktree-to-run-record lookup)
- [ ] 1.2 Add coverage in `tests/router/test_run_record.py` for `find-by-worktree`:
      exact match, no match, one malformed record file skipped alongside valid ones,
      and multiple non-terminal candidates resolving to the most recently started one.
      (Requirement: Worktree-to-run-record lookup)

## 2. Run record tracks its own worktree path

- [ ] 2.1 Wire `run_record.py set "$RUN" worktree "$WT"` into all three
      worktree-creation procedures in `skills/worktrail-go/references/subagent-prompts.md`
      (`#spec-worktree-setup`, `#change-spec-worktree-setup`,
      `#fix-branch-worktree-setup`), immediately after each `git worktree add`.
      (Requirement: Run record tracks its own worktree path)

## 3. Deletion liveness guard procedure

- [ ] 3.1 Add a new shared named section `#worktree-deletion-liveness-guard` to
      `skills/worktrail-go/references/subagent-prompts.md`: given `$WT`, a run-records
      directory, and the caller's `$INVOCATION_CONTEXT_DISPATCH_ID`, resolve the owning
      run record via `find-by-worktree`; if found, call `liveness` on it with
      `--dispatch-id`; refuse the deletion and report the conflict (owning run id,
      heartbeat age) when the result is `fresh: true` and `same_dispatch: false`;
      otherwise proceed with the caller's deletion unchanged. (Requirement: Deletion
      liveness guard)

## 4. Wire the guard into all documented deletion paths

- [ ] 4.1 Invoke `#worktree-deletion-liveness-guard` from both teardown sections in
      `skills/worktrail-go/references/subagent-prompts.md` — the `new`-pipeline
      teardown under `#worktree-lifecycle` and `#fix-branch-worktree-teardown` — before
      each section's `git worktree remove`/`branch -D` calls, passing that call site's
      own `$RUN`-derived run-records directory and `$INVOCATION_CONTEXT_DISPATCH_ID`.
      (Requirement: Guard applies uniformly across all documented deletion paths)
- [ ] 4.2 Invoke `#worktree-deletion-liveness-guard` from the prune step in
      `skills/worktrail-go/references/worktree-cleanup.md` before each confirmed-stale
      worktree's `git worktree remove`/`branch -D` calls, resolving the run-records
      directory from `worktrail-policy --json`'s `run_record_dir` (this flow has no
      `$RUN` of its own) and passing through whatever `$INVOCATION_CONTEXT_DISPATCH_ID`
      is set in the invoking shell. (Requirement: Guard applies uniformly across all
      documented deletion paths)

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm
      both pass.
