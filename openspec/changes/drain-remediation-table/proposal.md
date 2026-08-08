## Why

`drain.py`'s unattended sweep currently hardcodes one near-identical function per
recoverable stall category — `resume_quarantined_budget_exhausted` (PR #226) and
`resume_verify_pending` (PR #231/#232), each independently discovered only after a
human noticed a stalled spec on the `/go` dashboard that nothing was auto-resuming.
`dashboard.py`'s `detect_stage()` already enumerates a broader taxonomy of
stalled-but-recoverable stages than these two cover (e.g. `stale-bookkeeping` — impl
work already merged on base, only the task's `status:` needs flipping to
`completed`, no orchestrator re-run needed). Each new stall category currently
requires hand-writing a new finder + resume function + two call sites (pre-loop and
post-loop sweep) + a new summary-dict key, which is exactly why two near-identical
categories already exist and why the third (`stale-bookkeeping`) is still
unaddressed. Generalizing the sweep into a data-driven remediation table closes this
whole "spec silently stalls until a human happens to read the dashboard" bug class
at once, and makes each future safe category a one-line table entry instead of a new
function.

## What Changes

- Add a `StageRemediation` table in `drain.py` that pairs a stage finder with a
  remediation action, replacing the two hand-written per-stage sweep functions with
  one generic sweep engine that iterates the table.
- Add a third table entry for the `stale-bookkeeping` stage: flip the affected task(s)
  `status:` to `completed` (reusing `taskformats/devkit/schema.py`'s existing
  `set_status_completed`) and land a docs-only PR — the same procedure
  `worktrail-go`'s interactive `close-stale` dispatch action already documents by
  hand, now automated.
- Extend `dashboard.py`'s `detect_stage()` to surface the stale task ids it already
  computes (`_pending_impl_stale`/`_pending_tail_stale`) as a structured field on the
  stage-`stale-bookkeeping` result, instead of only formatting them into the
  human-readable `next_action` string, so `drain.py` can consume them without
  re-deriving the same git-tracked-file check.
- Preserve `resume_quarantined_budget_exhausted` and `resume_verify_pending` as
  public functions with unchanged signatures (both existing test suites and the
  `/go` interactive-dispatch code call them directly) — each becomes a thin call
  into the shared sweep engine rather than a hand-rolled loop.
- Keep the `drain()` summary dict's existing `resumed_quarantines` /
  `resumed_verify_pending` keys unchanged for backward compatibility; add a new
  `resumed_stale_bookkeeping` key alongside them.
- **Explicitly excluded, by design:** the `orchestrator-stuck` stage
  (`fanout_failed`) is never added to the remediation table — `routes.md` §E and
  `detect_stage()` both document it as unsafe to silently re-launch; it still
  requires human recovery.

## Capabilities

### New Capabilities
- `drain-stage-remediation-table`: the data-driven remediation table in `drain.py`
  (finder + action per stage, generic sweep loop, per-finding error isolation) that
  the unattended sweep iterates every pass, and the `stale-bookkeeping` remediation
  action (status-flip + docs-only PR) registered in it.

### Modified Capabilities
- `drain-verify-pending-resume`: `resume_verify_pending`'s implementation now
  delegates to the shared sweep engine instead of its own hand-rolled loop; its
  public signature, return shape, and log-label substring (`resume-verify-pending`)
  are unchanged, so this capability's existing acceptance criteria still hold.

## Impact

- `src/worktrail/drain/drain.py`: new `StageRemediation` table + generic sweep
  engine; `resume_quarantined_budget_exhausted` and `resume_verify_pending`
  refactored onto it; new `find_stale_bookkeeping_specs` finder and
  `close_stale_bookkeeping` action; `drain()`'s summary dict gains
  `resumed_stale_bookkeeping`.
- `src/worktrail/router/dashboard.py`: `detect_stage()`'s `stale-bookkeeping` branch
  gains a structured `stale_task_ids` field on its returned info dict (additive,
  does not change the `next_action` string or any other existing field).
- `tests/drain/test_drain.py`: existing coverage for the two current functions must
  keep passing unmodified; new coverage added for the table engine and the
  stale-bookkeeping remediation.
- No change to `worktrail-go`'s interactive `close-stale` dispatch action — it stays
  as the human-driven equivalent for a repo not covered by an unattended
  `--repos-root` sweep.
