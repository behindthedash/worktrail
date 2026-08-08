## 1. Detection helper

- [x] 1.1 Add `find_verify_pending_specs(repos_root, go_repo=None)` to `src/worktrail/drain/drain.py`, mirroring `find_resumable_quarantines()`'s shape: discover repo names via `discover_repo_names`, filter to `go_repo` when given, call `dashboard.scan(repo_path / "docs" / "specs")` per repo, keep rows where `stage == "verify-pending"`, resolve each hit's `spec_rel` via the existing `resolve_spec_rel()`, skip hits with no resolvable spec_rel. Return the same `{"repo", "repo_name", "spec_id", "spec_rel"}` shape `find_resumable_quarantines` returns.
- [x] 1.2 Import `dashboard` (or the specific `scan` symbol) from `..router.dashboard` in `drain.py`.

## 2. Resume sweep

- [x] 2.1 Add `resume_verify_pending(repos_root, go_repo, agent, timeout, spawner, log)` to `drain.py`, mirroring `resume_quarantined_budget_exhausted()`'s body: iterate `find_verify_pending_specs(repos_root, go_repo)`, build each resume command via the existing `build_full_real_resume_command()` and `_base_branch_for()`, log with a `resume-verify-pending:` / `resume-verify-pending result:` prefix (distinct from the quarantine sweep's `resume-quarantine:` prefix), append each `{"repo", "spec_id", "exit_code"}` result, continue past a failing spec rather than stopping.

## 3. Wiring into the drain loop

- [x] 3.1 In `drain()`, add a `resumed_verify_pending: List[Dict[str, Any]] = []` accumulator alongside the existing `resumed_quarantines`.
- [x] 3.2 Call `resume_verify_pending(...)` at the pre-loop sweep point, immediately after the existing `resume_quarantined_budget_exhausted(...)` call, using the same `config.repos_root`/`config.go_repo`/`active_agent`/`config.iteration_timeout`/`spawner`/`log` arguments and the same `if config.repos_root is not None and not config.dry_run:` guard.
- [x] 3.3 Call `resume_verify_pending(...)` at the post-loop re-sweep point, immediately after the existing post-loop `resume_quarantined_budget_exhausted(...)` call, under the same `if config.repos_root is not None and not config.dry_run and state.iteration > 0:` guard.
- [x] 3.4 Add `"resumed_verify_pending": resumed_verify_pending` to the `summary` dict returned by `drain()`, alongside the existing `"resumed_quarantines"` key.

## 4. Tests

- [x] 4.1 Add `test_find_verify_pending_specs_discovers_across_repos` to `tests/drain/test_drain.py`: build a `tmp_path` fixture repo with a spec whose run journal has `integrate_complete: true` and a non-`MERGED` group whose PR is not present in the (empty/no-op) git history, assert `find_verify_pending_specs(tmp_path)` returns that spec.
- [x] 4.2 Add `test_find_verify_pending_specs_excludes_non_verify_pending_stages`: a spec in `done`/`ready-to-implement` stage is not returned.
- [x] 4.3 Add `test_find_verify_pending_specs_skips_spec_with_no_resolvable_path`: a journal referencing a spec id whose folder no longer exists under either `docs/specs/` or `openspec/changes/` is skipped, mirroring `test_find_resumable_quarantines_skips_spec_with_no_resolvable_path`.
- [x] 4.4 Add `test_find_verify_pending_specs_go_repo_filter`: with two repos under `tmp_path`, passing `go_repo="repo-b"` only returns hits from `repo-b`, mirroring `test_find_resumable_quarantines_go_repo_filter`.
- [x] 4.5 Add `test_resume_verify_pending_invokes_full_real_once_per_spec`: a fake spawner records one `full-real` invocation per detected spec with no `--fresh` flag, mirroring `test_resume_quarantined_budget_exhausted_invokes_full_real_once_per_spec`.
- [x] 4.6 Add `test_resume_verify_pending_no_hits_is_noop`: no verify-pending specs found → spawner never called, empty list returned, mirroring `test_resume_quarantined_budget_exhausted_no_resumable_is_noop`.
- [x] 4.7 Add `test_resume_verify_pending_one_failure_does_not_block_others`: two detected specs, spawner returns non-zero exit for the first, assert the second is still attempted and both results are present in the returned list.
- [x] 4.8 Add/extend a `drain()`-level test asserting both `resumed_quarantines` and `resumed_verify_pending` keys are present in the returned summary dict when `--repos-root` is configured, and that `resume_verify_pending` is invoked at both the pre-loop and post-loop sweep points under the same conditions as the existing quarantine sweep.

## 5. Verification

- [x] 5.1 [cleanup] Run `PYTHONPATH=src pytest tests/drain/test_drain.py -q` and confirm all new and existing tests pass.
- [x] 5.2 [cleanup] Run the full `pre_pr_cmd` (`PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`) before opening the PR.
