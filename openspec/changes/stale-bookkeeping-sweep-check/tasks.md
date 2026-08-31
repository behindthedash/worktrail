## 1. Stale-bookkeeping check module

- [x] 1.1 Add `src/worktrail/router/spec_sync_sweep_stale_bookkeeping_check.py` with
      `check_repo_stale_bookkeeping(repo: Path) -> dict[str, Any]`, mirroring
      `spec_sync_sweep_checkbox_check.py`'s shape: the whole body runs inside one
      `try/except Exception`, returning `{"repo": str(repo), "findings": [...], "error": None}`
      on success or `{"repo": str(repo), "findings": [], "error": str(exc)}` on failure.
      (Requirement: Stale-bookkeeping check runs as a third independent per-repo check)
- [x] 1.2 Inside the try block, call `dashboard.scan(repo / "docs" / "specs")` (the same call
      `seed_backlog.py` already makes) and filter its rows to `stage == "stale-bookkeeping"`.
      Do not call `_pending_impl_stale`/`_pending_openspec_stale` directly and do not
      re-implement any git-tracked/freshness comparison. (Requirement: Stale-bookkeeping check
      runs as a third independent per-repo check)
- [x] 1.3 For each matching row, emit one finding per id in `row["stale_task_ids"]`:
      `{"format": "openspec" if row.get("format") == "openspec" else "devkit", "spec_id":
      row["id"], "task_id": <id>, "next_action": row.get("next_action"), "files": <files list
      for that task if the row's own loaded task data carries a non-empty "files", else []>}`.
      For the OpenSpec case, read `files` off the matching entry in `row["tasks"]`; for the
      devkit case, `scan()`'s row does not carry per-task file data, so `files` is `[]` there.
      (Design: Findings carry the stage/next_action/stale_task_ids fields scan() already
      computes)

## 2. Stale-bookkeeping brief module

- [x] 2.1 Add `src/worktrail/router/spec_sync_sweep_stale_bookkeeping_brief.py` with
      `DRIFT_SOURCE = "stale-bookkeeping-sweep"` and
      `file_stale_bookkeeping_brief(repo: Path, findings: list[dict], queue_base: Path) ->
      Path`, mirroring `spec_sync_sweep_checkbox_brief.py`'s structure (`_slug`, `_brief_id`,
      `_render`, queue-dir write, `validate_brief(path, required=("id", "status", "focus"))`
      before returning). (Requirement: Exactly one dedup'd Drift Brief per repo)
- [x] 2.2 `_render()` writes YAML frontmatter with `drift-source: stale-bookkeeping-sweep` and a
      `drift-findings` list (one entry per finding: `format`, `spec_id`, `task_id`,
      `next_action`, `files`), followed by a `## Focus` body listing every finding as one line:
      spec/change id, task id, and `next_action`. Exactly one file is written regardless of how
      many findings are passed in. (Requirement: Exactly one dedup'd Drift Brief per repo)

## 3. Wire into `spec_sync_sweep.py`

- [ ] 3.1 Import `file_stale_bookkeeping_brief` and `check_repo_stale_bookkeeping` in
      `spec_sync_sweep.py`, alongside the existing checkbox imports.
- [ ] 3.2 Extend `_empty_record()` with `"stale_bookkeeping_drifted": []`,
      `"stale_bookkeeping_filed": []`, `"stale_bookkeeping_skipped_existing": []`, and
      `"stale_bookkeeping_failed": []`. (Requirement: The sweep's run record and CLI summary
      report the stale-bookkeeping check)
- [ ] 3.3 In `run_sweep()`'s per-repo loop, after the existing checkbox-drift check, add a third
      independent block: call `check_repo_stale_bookkeeping(repo)`; on `error is not None`
      append to `stale_bookkeeping_failed`; on non-empty `findings`, append to
      `stale_bookkeeping_drifted`, look up
      `find_unresolved_drift_brief(repo, queue_base, drift_source="stale-bookkeeping-sweep")`,
      and either append to `stale_bookkeeping_skipped_existing` (existing unresolved brief) or
      call `file_stale_bookkeeping_brief(...)` and append to `stale_bookkeeping_filed`. This
      block performs no git operation and no task-status write-back against the checked repo —
      filing the Drift Brief into the queue is its only side effect. (Requirement:
      Stale-bookkeeping check is independent of the sweep's other two checks) (Requirement: The
      sweep never mutates task status or opens a PR)
- [ ] 3.4 Extend `main()`'s human-readable summary `print(...)` call to append a third clause
      reporting `stale_bookkeeping_drifted`/`stale_bookkeeping_filed`/
      `stale_bookkeeping_skipped_existing`/`stale_bookkeeping_failed` counts, matching the
      existing `checkbox-drift: ...` clause's phrasing. (Requirement: The sweep's run record and
      CLI summary report the stale-bookkeeping check)

## 4. Tests

- [x] 4.1 Add `tests/router/test_spec_sync_sweep_stale_bookkeeping_check.py`: a repo whose
      `dashboard.scan()` output includes a `stage: "stale-bookkeeping"` row yields matching
      findings; a repo with no such row yields `{"findings": [], "error": None}`; a repo whose
      scan raises is captured in `error` instead of propagating.
- [ ] 4.2 Add `tests/router/test_spec_sync_sweep_stale_bookkeeping_brief.py`: filing with
      multiple findings writes exactly one `.md` file under `queue_base/queue/` with
      `drift-source: stale-bookkeeping-sweep`, valid per `validate_brief`, and a body listing
      every finding.
- [ ] 4.3 Update `tests/router/test_spec_sync_sweep.py`'s `run_sweep()` coverage to add
      three-way independence cases: (a) a repo drifted on stale-bookkeeping only gets exactly
      one stale-bookkeeping brief and no spec-sync/checkbox brief; (b) a repo drifted on all
      three gets all three briefs, governed by their own dedup lookups; (c) the
      stale-bookkeeping check erroring for a repo does not block that repo's other two checks,
      or any other repo's stale-bookkeeping check, from running; (d) an already-unresolved
      stale-bookkeeping brief suppresses a new one via `skipped_existing` while a resolved
      (`status: done`) one does not.
- [ ] 4.4 Update `tests/router/test_spec_sync_sweep_e2e.py` if it asserts on the full record
      shape or summary line, so it covers the new `stale_bookkeeping_*` fields end-to-end.
- [ ] 4.5 [cleanup] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both are green. Tagged
      `[cleanup]` (tail kind): verification-only, no file scope of its own — depends on every
      prior task landing first.

## 5. Documentation

- [ ] 5.1 Update `spec_sync_sweep.py`'s module docstring to describe the stale-bookkeeping check
      alongside the existing spec-sync-drift and checkbox-drift descriptions, including the
      three-way independence statement and the dedup key
      (`drift_source="stale-bookkeeping-sweep"`).
- [ ] 5.2 Add a deployment note (in the same docstring, near the existing cron example) stating
      that no new crontab entry is needed — this check rides the existing weekly
      `spec-sync-sweep.sh` invocation — without editing any file in the sibling `devops` repo.
