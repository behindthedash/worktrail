## Why

`_watch_ci` (`src/worktrail/router/land_pr.py:849`) gives "no checks reported yet" a short
grace period before entering the main watch loop, sized by two hardcoded module constants:
`_NO_CHECKS_GRACE_ATTEMPTS = 3` and `_NO_CHECKS_POLL_INTERVAL_S = 3`
(`land_pr.py:123-124`). That is **9 seconds**, and it does not move with `--watch-timeout`
(`land_pr.py:1917`, default 600s) — the operator-facing knob that says how long this run is
willing to wait for CI. Nothing else in the module is sized this way: `WATCH_REISSUE_MAX`
multiplies against `watch_timeout_s`, so the main loop's patience scales while the grace
period in front of it does not.

The consequence is asymmetric and wrong in the expensive direction. When the grace period is
spent, `_watch_ci` returns `budget_exhausted` (`land_pr.py:887-900`), which `land_pr()` maps
to `outcome="ceiling"` / `final_status="failed_recoverable"` with merge_result
`"checks still pending at watch budget"` (`land_pr.py:1650`). So a PR whose workflow runs
simply took more than 9 seconds to attach — a real and common race right after
`gh pr create`, which is exactly why the grace period exists — is reported as a run that
exhausted its watch budget, needing reconciliation, having spent 1.5% of the budget the
operator actually granted. The message is also false on its face: the checks were not pending
at the *watch* budget; the watch was never entered.

(Work-queue brief `20260920-151833-land-pr-ceiling-on-pending`.)

## What Changes

- The registration grace period is derived from `watch_timeout_s` — already a parameter of
  `_watch_ci` — instead of being a fixed attempt count. The poll interval stays
  `_NO_CHECKS_POLL_INTERVAL_S`; the number of attempts is what scales.
- The current 3 attempts become a **floor**, so a very small `--watch-timeout` never makes the
  grace period shorter than it is today, and the grace window never exceeds one watch window
  (`watch_timeout_s`) — checks that have not attached within the time a single watch would
  have run are not going to attach.
- When the scaled grace is spent, the reported `merge_result` distinguishes "checks never
  registered" from the main watch loop's "checks still pending at watch budget", so a
  reconciler can tell which of the two budgets was actually spent. The outcome remains
  `ceiling` / `failed_recoverable` — that classification is deliberate (see the `_watch_ci`
  docstring) and is not being changed.

## Capabilities

### New Capabilities
- `land-pr-check-registration-grace`: the registration grace period ahead of the CI watch,
  its sizing relative to the run's watch budget, and how its exhaustion is reported.

### Modified Capabilities

## Impact

- `src/worktrail/router/land_pr.py`: grace-attempt count derived from `watch_timeout_s` in
  `_watch_ci`, a distinct exhaustion reason threaded back to `land_pr()`'s ceiling branch.
- `tests/router/test_land_pr_required_contexts.py`: its
  `test_non_required_only_checks_keep_polling_then_exhaust` asserts the probe count equals
  `_NO_CHECKS_GRACE_ATTEMPTS` against a 60s watch timeout, so it moves to the derived count.
- No CLI flag, `LandRequest`, or `LandOutcome` shape change; `--watch-timeout`'s existing
  meaning is unchanged, it simply now also governs the grace period in front of the watch.
