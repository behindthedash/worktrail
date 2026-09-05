## 1. OpenSpec-aware stale-bookkeeping closeout

- [ ] 1.1 In `src/worktrail/drain/drain.py`: (a) `find_stale_bookkeeping_specs` adds
      `"format": row.get("format") or "devkit"` to each finding dict; (b) `close_stale_bookkeeping`
      reads `finding.get("format", "devkit")` and moves the `_resolve_stale_task_path` /
      "no TASK-*.md found" pre-check under the devkit branch so it never runs for an OpenSpec
      finding; the open-PR pre-check, `_reset_stale_bookkeeping_worktree`, `git worktree add`, and
      the `finally` teardown stay shared; (c) inside the worktree, the devkit branch is unchanged
      (`set_status_completed` per task path, `git add <paths>`), and the OpenSpec branch calls
      `flip_and_archive(wt, spec_id, task_ids, timeout=timeout)` imported from
      `worktrail.router.close_stale_openspec`, raises `RuntimeError` when `result["error"]` is set,
      returns the existing `{"repo","spec_id","task_ids","pr_url": None}` no-op shape (with a log
      line) when `result["flipped"]` is empty and `result["archived"]` is false, otherwise runs
      `git add -A`, commits `chore(<spec_id>): close stale bookkeeping (<ids>)`, `push --force -u
      origin <branch>`, and calls the existing `_land_remediation_pr` with a body stating the stale
      task(s) were flipped in `tasks.md` and the change was archived via `openspec archive`. Do not
      touch `archive_openspec_change`. In `tests/drain/test_drain.py`: add a failing-first
      regression test `test_close_stale_bookkeeping_openspec_flips_and_archives` that builds an
      OpenSpec change fixture (`openspec/changes/<id>/{proposal.md,tasks.md}` with `- [ ] 1.1 ...`,
      committed and pushed to `origin/dev`, mirroring `_write_stale_bookkeeping_spec`), passes a
      finding with `"format": "openspec"` and `"stale_task_ids": ["1.1"]`, monkeypatches
      `drain.land_pr` and `drain.subprocess.run` like
      `test_close_stale_bookkeeping_flips_status_and_opens_pr`, and also monkeypatches
      `worktrail.router.close_stale_openspec.subprocess.run` to fake `openspec archive` by moving
      `openspec/changes/<id>` to `openspec/changes/archive/<id>` (returning returncode 0); assert
      the result dict has the PR URL, `land_pr` was called once, and on branch
      `fix/close-stale-<id>` `git show` reports `openspec/changes/archive/<id>/tasks.md` with `[x]
      1.1` and no `openspec/changes/<id>/tasks.md`. Add a test that the OpenSpec branch raises
      `RuntimeError` when `flip_and_archive` reports an error (e.g. `tasks.md` missing) with no
      `land_pr` call. Add `test_find_stale_bookkeeping_specs_carries_format` asserting a devkit
      finding has `format == "devkit"`. Keep every existing `find_stale_bookkeeping_specs` and
      `close_stale_bookkeeping` test passing unchanged. (Requirements: Stale-bookkeeping
      remediation — "A spec has one or more stale-bookkeeping tasks", OpenSpec scenario, flip-and-archive error scenario)

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/drain/test_drain.py
      tests/router/test_close_stale_openspec.py` and `openspec validate --strict` from the repo
      root and confirm both pass; depends on 1.1.
