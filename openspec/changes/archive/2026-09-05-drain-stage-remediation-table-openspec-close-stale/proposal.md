## Why

The drain resume pass `close-stale-bookkeeping` (`src/worktrail/drain/drain.py`,
`close_stale_bookkeeping` + `_resolve_stale_task_path`) assumes every
stale-bookkeeping finding is a devkit spec with `docs/specs/<id>/tasks/TASK-*.md`
files. `dashboard.scan()` also emits `stale-bookkeeping` rows for OpenSpec
changes (`format == "openspec"`, `stale_task_ids` like `2.1`), and
`find_stale_bookkeeping_specs` passes those rows into the action unchanged. The
action then raises `RuntimeError("no TASK-*.md found for <repo> <spec>: 2.1, ...")`
on every sweep for every OpenSpec finding — the 2026-09-02 drain log shows this
for datalena `continue-on-error-required-check-ci-guardrail` and
`router-capability-guard-coverage`. The finding never resolves, so it recurs
nightly and never gets its docs-only closeout PR.

## What Changes

- `find_stale_bookkeeping_specs` carries the dashboard row's `format` into each
  finding (`finding["format"]`, defaulting to `"devkit"` when absent).
- `close_stale_bookkeeping` branches on that format. The devkit path is
  unchanged. The OpenSpec path, inside the same short-lived fix-branch
  worktree lifecycle, calls `worktrail.router.close_stale_openspec.flip_and_archive`
  to flip the stale `tasks.md` checkboxes and run `openspec archive -y`, raises
  `RuntimeError` on `result["error"]`, returns the existing no-PR no-op shape
  when nothing flipped and nothing to archive, then stages everything, commits,
  force-pushes, and lands the PR through the existing `_land_remediation_pr`
  with a body saying the change was flipped and archived. It never looks for
  `TASK-*.md` for an OpenSpec finding.
- `archive_openspec_change` (the complete-stage archive remediation) is not
  touched.
- Regression tests in `tests/drain/test_drain.py`: a failing-first test with an
  OpenSpec stale-bookkeeping fixture asserting the fix branch has the checkbox
  flipped and the change moved under `openspec/changes/archive/`, plus a
  `find_stale_bookkeeping_specs` test asserting `format` is carried.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `drain-stage-remediation-table`: the "Stale-bookkeeping remediation"
  requirement gains an OpenSpec scenario (flip `tasks.md` checkboxes, run
  `openspec archive`, commit, open the docs-only PR) alongside the existing
  devkit scenario.

## Impact

- `src/worktrail/drain/drain.py`: `find_stale_bookkeeping_specs`,
  `close_stale_bookkeeping`.
- `tests/drain/test_drain.py`: new regression tests; existing stale-bookkeeping
  tests keep passing.
- Reuses `worktrail.router.close_stale_openspec.flip_and_archive` (no change to
  that module).
- No CLI, summary-dict, or `REMEDIATION_TABLE` shape changes.
