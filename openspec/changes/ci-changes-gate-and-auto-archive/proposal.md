## Why

Measured 2026-08-13 across 10 PRs: bookkeeping-only PRs (archive PRs #364/#368,
version-bump PR #365) each ran the full `Lint, Test & Build` suite in CI —
`pytest`, the orchestrator golden regression, and a package build — despite
touching no `src/worktrail/**`. `pre_pr_gate.py` already has a `docs_only_paths`
fast path for local preflight, but `ci.yml` has no equivalent gate, so every
bookkeeping PR still pays the full CI runtime. Separately, closing out a
shipped OpenSpec change today costs a second hand-driven chore PR (archive +
occasionally a version bump) even though `dashboard.scan()` already computes
exactly the signal needed to know a change is ready: `stage: "complete"` fires
only once every task is checked, the run journal shows the code merged
(`verify-pending` already claims the unmerged case), and the delta is
reconciled into `openspec/specs/` (`sync-pending` already claims the unsynced
case). Together these two chores account for the ~15-20 minutes of per-item
shipping ceremony this change removes.

## What Changes

- Add a `changes` job to `ci.yml` using `dorny/paths-filter` with
  `predicate-quantifier: every` (mirroring datalena's `qa-pipeline.yml`
  pattern) to classify a PR/push diff as bookkeeping-only — every changed path
  matches `openspec/**`, `docs/**`, `**/*.md`, or is a `pyproject.toml` change
  touching only the `version = ` line (verified the same way
  `version_bump_check.sh` already does, not just a path glob, so a real
  dependency/config edit inside `pyproject.toml` never false-passes).
- Gate the existing `lint-test-build` job on `needs.changes.outputs.bookkeeping
  == 'false'`; add a `bookkeeping-bypass` job that posts a green
  `Lint, Test & Build` check via `github.rest.checks.create` when
  `bookkeeping == 'true'`, so the branch ruleset's required status check still
  resolves without running pytest/golden-regression/build.
- Leave `Version bump check` and `Scope check` (separate required workflows)
  unchanged — both already run cheap scripts, not the full suite, and version
  bump already treats a change with no `src/worktrail/**` diff as
  not-required.
- Add a `close-openspec-change` remediation row to `drain.py`'s
  `REMEDIATION_TABLE` (mirroring `close_stale_bookkeeping`'s fix-branch
  worktree + docs-only PR pattern): a finder over `dashboard.scan()` results
  selecting OpenSpec changes (`format == "openspec"`) at `stage == "complete"`
  (already the exact "all tasks done, code merged, delta reconciled" signal —
  see `_safe_detect_openspec`'s `next_action: "archive"`), and an action that
  runs `openspec archive -y <change-id>` in a short-lived worktree, commits
  the resulting archive move, and opens a docs-only PR carrying
  `go:risk-low`.
- Re-entrant like `close_stale_bookkeeping`: an already-open PR for the
  archive branch is detected and returned as-is rather than re-run.

## Capabilities

### New Capabilities
- `ci-bookkeeping-changes-gate`: classify a PR/push diff as bookkeeping-only
  (docs/openspec/version-only) and skip the full `Lint, Test & Build` suite
  for it while still resolving the required status check.

### Modified Capabilities
- `drain-stage-remediation-table`: add a `close-openspec-change` table row
  (finder + action) for auto-archiving a task-complete, merged, reconciled
  OpenSpec change; extend the summary dict with a `resumed_openspec_archive`
  key.

## Impact

- `.github/workflows/ci.yml`: new `changes` + `bookkeeping-bypass` jobs, `if:`
  guard on `lint-test-build`.
- `src/worktrail/drain/drain.py`: new finder, action, and `REMEDIATION_TABLE`
  row; `sweep_remediations()` summary gains `resumed_openspec_archive`.
- `tests/drain/test_drain.py`, a new CI workflow test/lint check for the
  `changes` job's filter behavior.
- `openspec/specs/drain-stage-remediation-table/spec.md`: updated by the
  normal sync/archive lifecycle once this change is implemented.
