## Why

`drain.py`'s nightly `REMEDIATION_TABLE` sweep (`quarantined_budget_exhausted`,
`verify_pending`, `stale_bookkeeping`, `sync_pending`, `openspec_archive`) has no way
to tell "remediated" from "looked remediated." A remediation action can report success
(no exception, `exit_code: 0`) every single night while the underlying `(repo, spec_id)`
finding keeps recurring, because the finder that feeds it never actually clears. This
exact failure class shipped as PR #515 (`sync_pending`'s action was a no-op that still
reported success) — and it was caught only because a human manually cross-referenced
three consecutive nights of raw `~/.worktrail/drain-logs/*.json` by hand. Nothing in the
system itself surfaced it. The same silent-recurrence bug can happen in any of the other
four table rows (e.g. `archive_openspec_change` silently failing to push, or
`close_stale_bookkeeping`'s PR never getting picked up) and today it would take another
multi-night manual log audit to notice.

## What Changes

- Add a persisted, cross-sweep history of remediation findings, keyed by
  `(remediation_key, repo_name, spec_id)`, so `drain()` can tell whether the same finding
  was reported by a table row's finder — with that row's action completing without
  raising — on the current sweep and on the immediately preceding ones.
- Add a stuck-remediation detector that flags any identity recurring for N consecutive
  sweeps (default N=3, matching the PR #515 discovery) despite each of those sweeps'
  action reporting apparent success. Applies uniformly across all five
  `REMEDIATION_TABLE` rows — a future regression in any row is caught the same way,
  with no per-row wiring.
- Add a `stuck_remediations` key to `drain()`'s returned/`--json` summary dict, listing
  every identity that crossed the threshold this run, so the finding is visible without
  reading raw log files.
- Log a `stuck remediation: ...` line (same style as the existing `pending human
  approval:` / `decisions awaiting a human:` lines) whenever the detector flags an
  identity, so it shows up in the nightly drain output directly.
- Bound the persisted history file's growth (prune identities/records not seen within a
  configurable retention window) so it does not grow unbounded across months of nightly
  runs.

Out of scope for this change (different repo, different PR — captured as a deferred
handoff item): rendering the new `stuck_remediations` summary key in
`worktrail-drain-digest.py` (devops repo). This change makes the data available in the
JSON summary the digest already reads; wiring the digest's own display of it is separate
work in a separate repository.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `drain-stage-remediation-table`: adds a stuck-remediation detection requirement that
  tracks each remediation finding across sweeps and flags recurrence despite apparent
  per-sweep success, and extends the summary dict's backward-compatibility requirement
  with the new `stuck_remediations` key.

## Impact

- `src/worktrail/drain/drain.py`: `sweep_remediations`/`drain()` gain a post-sweep
  history-record-and-detect step; the returned summary dict gains `stuck_remediations`;
  a new CLI flag exposes the consecutive-sweep threshold.
- New module for the persisted history (load/save/prune), following the existing
  `agent_capacity.py` atomic-write + `flock` pattern for machine-local state under
  `worktrail_home()`.
- `tests/drain/test_drain.py` (or a new `tests/drain/test_stuck_remediation.py`): unit
  coverage for the pure streak-detection logic plus the persistence round-trip.
- No change to any of the five existing finder/action functions' behavior or signatures.
