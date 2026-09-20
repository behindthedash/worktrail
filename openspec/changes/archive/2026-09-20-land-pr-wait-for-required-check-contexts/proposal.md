## Why

`_checks_registered` (`src/worktrail/router/land_pr.py:797`) returns `True` as soon as
`gh pr checks` reports **any** check, and `_watch_ci` (`land_pr.py:826`) then leaves the
no-checks grace loop and watches only that set. Nothing in the module compares the observed
checks against the base branch's ruleset-required status check contexts
(`grep -rn required_status_checks src/` hits only `router/automerge_preflight.py` and
`onboarding/`, never `land_pr.py`). A PR whose fast third-party statuses register first --
the two Vercel statuses on behindthedash PR #86 -- therefore ends the grace period, gets
watched to a clean exit, and is reported as a settled, terminal pass while the required
GitHub Actions contexts had not even been created yet. The pipeline then proceeds to merge
handling on CI it never observed, which is the same class of failure the no-checks grace
period exists to close (Requirement: CI watch runs to a classified terminal outcome).

`automerge_preflight.required_status_check_contexts()` already reads exactly the set needed,
so this is a wiring and gating change, not a new integration.

Distinct from the active change `land-pr-watch-follow-head-sha-and-rebase-bot-commits`, which
addresses head-SHA moves and bot-commit rebases in the same loop but never the required-context
set. (Work-queue brief `20260920-151038-land-pr-settles-before-required`.)

## What Changes

- The landing pipeline reads the base branch's required status check contexts once before the
  CI watch and passes them into `_watch_ci`.
- "Checks registered" becomes "every required context is present among the PR's reported
  checks". The existing grace loop keeps polling until coverage is reached, and reports
  `budget_exhausted` (-> `ceiling`) rather than `settled` when the grace period is spent with
  required contexts still missing -- exactly as it does today for zero checks.
- A blocking watch that exits zero while required contexts are still absent is not terminal:
  the loop re-enters against the remaining re-issue budget instead of returning `settled`.
- When the required-context query fails, or the branch has zero required contexts configured,
  behaviour is exactly what it is today: any reported check ends the grace period, and a zero
  watch exit settles.

## Capabilities

### New Capabilities
- `land-pr-required-context-coverage`: the CI watch settles only once the base branch's
  ruleset-required status check contexts have all been observed.

### Modified Capabilities

## Impact

- `src/worktrail/router/land_pr.py`: `_checks_registered` and `_watch_ci` gain a
  required-contexts parameter; `land_pr()` resolves the contexts before the watch.
- `tests/router/`: new test module (kept out of `test_land_pr.py`, whose task chain already
  saturates the compile same-file gate).
- No CLI, `LandRequest`, or `LandOutcome` change. A repo with no ruleset-required contexts, or
  an unreadable ruleset, lands exactly as it does today.
