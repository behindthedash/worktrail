## 1. Scale the registration grace to the watch budget

- [x] 1.1 In `src/worktrail/router/land_pr.py`: add a module-level helper that derives the
      number of registration grace attempts from `watch_timeout_s` — the attempt count is
      `watch_timeout_s // _NO_CHECKS_POLL_INTERVAL_S`, floored at the existing
      `_NO_CHECKS_GRACE_ATTEMPTS` (which stays as the floor constant, keeping today's
      behaviour for a very short `--watch-timeout`) and capped so the total grace never
      exceeds one `watch_timeout_s` window. Use it for the grace loop's range in `_watch_ci`
      (`land_pr.py:887`) in place of the bare constant, leaving `_NO_CHECKS_POLL_INTERVAL_S`
      and the merged/registered checks inside the loop untouched. Then have that loop's
      exhaustion return (`land_pr.py:893-899`) carry a marker distinguishing it from the main
      watch loop's own `budget_exhausted` return, and have `land_pr()`'s `budget_exhausted`
      branch (`land_pr.py:1637-1659`) use a merge result naming unregistered checks for that
      case while keeping `"checks still pending at watch budget"` for the main loop, with the
      `ceiling` / `failed_recoverable` outcome and the `run_record.py finish` call otherwise
      unchanged; update the `_watch_ci` docstring's grace-period paragraph accordingly.
      (Requirements: Check-registration grace scales with the run's watch budget; Exhausted
      registration grace is reported distinctly from watch exhaustion.)
      In a new `tests/router/test_land_pr_registration_grace.py` using `FakeRun` from
      `tests/router/test_land_pr.py` with `time.sleep` patched, cover every scenario of both
      requirements: a watch timeout larger than the fixed grace probes more than
      `_NO_CHECKS_GRACE_ATTEMPTS` times; a watch timeout shorter than it still probes exactly
      `_NO_CHECKS_GRACE_ATTEMPTS` times; the attempt count times the poll interval never
      exceeds the watch timeout for a large timeout; registration on a probe beyond the fixed
      attempt count enters the main watch loop; a PR observed merged during the grace period
      returns settled without `budget_exhausted`; and, end-to-end through `land_pr()`, a spent
      registration grace yields `outcome="ceiling"`, `final_status="failed_recoverable"` and a
      merge result naming unregistered checks, while checks that register and then exhaust the
      main watch re-issue budget still yield `"checks still pending at watch budget"`.
      Finally, in `tests/router/test_land_pr_required_contexts.py`, update
      `test_non_required_only_checks_keep_polling_then_exhaust` — which asserts the probe count
      equals `land_pr._NO_CHECKS_GRACE_ATTEMPTS` while calling `_watch_ci` with a 60s watch
      timeout — to assert against the derived attempt count for that timeout instead.
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr_registration_grace.py, tests/router/test_land_pr_required_contexts.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate land-pr-no-checks-grace-scale-to-watch-budget --strict` and
      `worktrail-compile openspec/changes/land-pr-no-checks-grace-scale-to-watch-budget`.
