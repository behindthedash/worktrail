## 1. Creation-baseline helpers

- [x] 1.1 In `src/worktrail/router/dashboard.py`, add an `lru_cache`d
      `_dir_creation_timestamp(repo_value: str, directory_value: str) -> int | None` that runs
      `git log --format=%ct --reverse -- <directory_value>` in `repo_value` and returns the
      oldest commit's epoch timestamp, or `None` on git failure or no history (mirrors the
      existing `_rename_destinations` cache shape/error handling in the same module).
- [x] 1.2 Add an `lru_cache`d `_latest_commit_timestamp(repo_value: str, relative_value: str) ->
      int | None` that runs `git log -1 --format=%ct -- <relative_value>` and returns that
      commit's epoch timestamp, or `None` on git failure or no history.

## 2. Strengthen the shared shipped predicate

- [ ] 2.1 Change `_task_files_are_shipped(repo, files, tracked)` to
      `_task_files_are_shipped(repo, files, tracked, since_ts: int | None)`. For each declared
      file, after confirming it is tracked and present on disk (existing behavior) or resolves
      via `_moved_tracked_path` (existing behavior), additionally require
      `_latest_commit_timestamp(str(target_repo), final_relative_path) >= since_ts` (with
      `since_ts is None` always failing this check). A file that fails the check is not shipped;
      a task is shipped only when every declared file is. (Requirement: A pending task is stale
      only when its cached file scope shows evidence of shipped content)
- [ ] 2.2 Update the three existing calls inside `dashboard.py` (`_pending_impl_stale`,
      `_pending_openspec_stale`, `_pending_tail_stale`) to compute `since_ts =
      _dir_creation_timestamp(str(repo), str(spec_dir))` (devkit callers) or
      `_dir_creation_timestamp(str(repo), str(change_dir))` (OpenSpec caller) once per function
      call, and pass it through to `_task_files_are_shipped`.

## 3. Update `check_spec_collision.py`

- [ ] 3.1 In `verify()`, compute `since_ts = dashboard._dir_creation_timestamp(str(repo),
      str(spec_dir))` for the collision candidate's own `spec_dir` and pass it as the new
      argument to `_task_files_are_shipped`. Import `_dir_creation_timestamp` alongside the
      other `dashboard` helpers already imported at the top of the file.

## 4. Adjust existing test fixtures for the new creation baseline

- [ ] 4.1 In `tests/router/test_dashboard.py`'s `StaleBookkeeping._spec_dir()`, `git add` +
      `git commit` the spec's feature markdown file immediately after writing it, so every test
      in that class has a creation baseline before any "shipped" file is committed.
- [ ] 4.2 In `tests/router/test_dashboard.py`'s `OpenSpecStaleBookkeeping._change()`, `git add` +
      `git commit` `proposal.md`/`tasks.md` immediately after writing them, so every test in that
      class has a creation baseline before any "shipped" file is committed.
- [ ] 4.3 [e2e] Run the full `StaleBookkeeping` and `OpenSpecStaleBookkeeping` test classes and
      confirm every existing test still passes unmodified with the strengthened criterion (no
      assertion changes expected — only the two helper methods above change).

## 5. New coverage for the strengthened criterion

- [ ] 5.1 Add a `_commit_at`-style helper (explicit `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`) to
      `OpenSpecStaleBookkeeping` (or reuse one if this task's work already introduces a shared
      one) so the new tests can deterministically order "file committed before the change" vs.
      "change created" vs. "file committed/changed after the change," without relying on
      same-second wall-clock ordering.
- [ ] 5.2 Add `test_preexisting_unchanged_file_is_not_stale`: commit a file at `t0`, create+commit
      the change directory at `t1 > t0` referencing that file in its cached plan, and assert
      `_safe_detect_openspec` reports `stage: ready-to-implement` (not stale) because the file's
      last commit predates the change's creation baseline.
- [ ] 5.3 Add `test_new_file_since_creation_is_stale`: create+commit the change directory at
      `t0`, then commit a brand-new declared file at `t1 > t0`, and assert
      `_safe_detect_openspec` reports `stage: stale-bookkeeping` with that task's id in
      `stale_task_ids`.
- [ ] 5.4 Add `test_preexisting_file_modified_after_creation_is_stale`: commit a declared file at
      `t0`, create+commit the change directory at `t1 > t0` referencing it, then commit a content
      change to that same file at `t2 > t1`, and assert `_safe_detect_openspec` reports `stage:
      stale-bookkeeping` with that task's id in `stale_task_ids`.

## 6. Verify

- [ ] 6.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm the full suite is green, including
      `tests/router/test_dashboard.py` and `tests/router/test_check_spec_collision.py`.
- [ ] 6.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` (golden
      record/replay regression) and confirm it passes.
