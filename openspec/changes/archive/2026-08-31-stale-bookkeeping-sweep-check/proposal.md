## Why

`dashboard.py`'s `_pending_impl_stale()`/`_pending_openspec_stale()` already detect pending
tasks whose declared files shipped out-of-band and were never flipped to `completed` — but
only when a human or unattended `drain`/`auto` run triggers an interactive `/go` dashboard
scan against a given repo. Nothing schedules the check on its own, so a repo with stale
bookkeeping sits unreported until someone happens to look
(`docs/specs/research/stale-bookkeeping-automated-sweep-status.md`). `spec_sync_sweep.py`
already runs two independent, scheduled checks (spec-sync drift, checkbox-completion drift)
across every repo under `--repos-root` on the existing weekly cron. Adding stale-bookkeeping
as a third, equally independent check closes the gap using infrastructure that already exists,
rather than requiring someone to remember to run `/go`.

## What Changes

- Add `spec_sync_sweep_stale_bookkeeping_check.py`: a per-repo check that reuses
  `dashboard.scan()` (the same entry point `seed_backlog.py` already calls, itself backed by
  the unmodified `_pending_impl_stale`/`_pending_openspec_stale` machinery) to find every
  `stage: "stale-bookkeeping"` spec/change in a repo's `docs/specs/` and/or `openspec/changes/`
  tree, returning one finding per stale-pending task (task id, spec/change id, and the stale
  file evidence already computed by that machinery).
- Add `spec_sync_sweep_stale_bookkeeping_brief.py`: files exactly one dedup'd Drift Brief per
  repo into the work queue when stale-pending tasks are found, reusing
  `spec_sync_sweep_dedup.find_unresolved_drift_brief(repo, queue_base,
  drift_source="stale-bookkeeping-sweep")` so a repo with an outstanding unresolved brief from
  a prior run does not get a duplicate filed.
- Wire the new check into `spec_sync_sweep.py`'s `run_sweep()` as a third, fully independent
  per-repo check alongside the existing two: extend the run record with
  `stale_bookkeeping_drifted`/`stale_bookkeeping_filed`/`stale_bookkeeping_skipped_existing`/
  `stale_bookkeeping_failed` list fields (parallel to the existing `drifted`/`filed`/
  `skipped_existing`/`failed` and `checkbox_*` fields), and extend `main()`'s human-readable
  summary line to report it.
- Full test coverage mirroring the existing checkbox-drift-sweep tests: unit tests for the new
  check module and the new brief-filing module, plus an updated `run_sweep()` test verifying
  three-way independence (a repo drifted on exactly one of the three checks still gets exactly
  the right brief(s) filed, and a check erroring for a repo never blocks the other two checks
  for that same repo).
- Update `spec_sync_sweep.py`'s module docstring to describe the new check alongside the two
  existing ones, and note that no new crontab entry is needed — this rides the existing
  Monday-06:00 scheduled invocation (deployment note; the crontab itself lives in the sibling
  `devops` repo, out of scope for this change's own artifacts).

This does not modify `_pending_impl_stale`, `_pending_openspec_stale`, `dashboard.scan()`, or
any other detection logic — it is a scheduling/brief-filing wrapper around already-hardened
detection (most recently strengthened in PR #844), not new detection logic.

## Capabilities

### New Capabilities
- `stale-bookkeeping-sweep-check`: a scheduled, per-repo check inside `worktrail-spec-sync-sweep`
  that surfaces pending tasks whose implementation already shipped and files a dedup'd Drift
  Brief per repo, independent of the sweep's existing spec-sync-drift and checkbox-drift checks.

### Modified Capabilities
(none — `openspec-stale-bookkeeping-detection` continues to own the dashboard-scan detection
itself; this change only schedules and surfaces that existing detection, and does not change
its requirements)

## Impact

- New files: `src/worktrail/router/spec_sync_sweep_stale_bookkeeping_check.py`,
  `src/worktrail/router/spec_sync_sweep_stale_bookkeeping_brief.py`, plus mirrored test files
  under `tests/router/`.
- Modified files: `src/worktrail/router/spec_sync_sweep.py` (new import, run record fields,
  `main()` summary line, module docstring), `tests/router/test_spec_sync_sweep.py` and
  `tests/router/test_spec_sync_sweep_e2e.py` (independence coverage).
- Runtime dependency: `dashboard.scan()` and the private `_load_tasks` helper it wraps run one
  `git ls-files`/`git log` subprocess per candidate file per repo, same cost profile the
  interactive `/go` scan already pays — no new external calls, no network access.
- Deployment: none. The existing weekly cron (`~/bin/spec-sync-sweep.sh`, Monday 06:00, per
  `~/projects/devops/docs/ops/spec-sync-sweep.md`) already invokes `spec_sync_sweep.py`'s
  `main()`; this change rides that same invocation. No crontab edit is required, and none of
  this change's own artifacts touch the `devops` repo.
