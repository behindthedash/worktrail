## Why

`drain.py`'s unattended queue-drainer already resumes `budget_exhausted` `QUARANTINED`
specs via `resume_quarantined_budget_exhausted()`, but a larger, more common category
of stalled work is invisible to it: specs in the dashboard's `verify-pending` stage
(`dashboard.py`'s `_journal_verify_pending()` — implementation complete, group PRs still
need verify → merge → cleanup, `next_action: "resume full-real"`). `worktrail-go auto`
only ever claims work-queue briefs; it never reads the dashboard's Active Work section,
so a `verify-pending` spec sits stalled until a human happens to notice the dashboard
line and re-runs `full-real` by hand. Confirmed live 2026-08-07: `pullhook`
`001-relay-auth-and-item-lifecycle` and `003-rate-limiting-and-audit-log` both showed
"Needs verify / merge → resume full-real" in the `/go` dashboard with no automated path
to resolve it.

## What Changes

- Add a new sweep function to `src/worktrail/drain/drain.py`, mirroring the shape of the
  existing `resume_quarantined_budget_exhausted()`, that scans `--repos-root` for specs
  in the `verify-pending` stage (via `dashboard.py`'s existing `_journal_verify_pending()`
  detection) across every configured repo.
- Resume each detected spec with the same plain `worktrail-live full-real` re-run used by
  the quarantine sweep (no `--fresh`), reusing `resolve_spec_rel()` and
  `build_full_real_resume_command()`.
- Wire the new sweep into `drain.py`'s existing unattended loop alongside the quarantine
  sweep, so a single drain invocation covers both stalled categories.
- No change to `_journal_verify_pending()`'s existing detection logic or the dashboard's
  own rendering — this change only adds an active resume path that consumes the same
  detection dashboard.py already performs for visibility.

## Capabilities

### New Capabilities
- `drain-verify-pending-resume`: unattended queue-drainer sweep that detects specs stuck
  in the verify-pending stage (impl complete, PR not yet merged/cleaned up) across a
  repos-root and resumes them via `worktrail-live full-real`, without requiring a human
  to notice the dashboard and intervene manually.

### Modified Capabilities
(none — this is additive; no existing spec's requirements change)

## Impact

- `src/worktrail/drain/drain.py` — new sweep function + wiring into the main drain loop.
- Read-only dependency on `src/worktrail/router/dashboard.py`'s `_journal_verify_pending()`
  (or an equivalent exported helper) — no changes to that module.
- `tests/drain/` — new coverage for the verify-pending sweep (detection + resume-command
  construction + wiring), mirroring existing `resume_quarantined_budget_exhausted` tests.
- No CLI flag/behavior change for existing quarantine-sweep users; this is additive.
