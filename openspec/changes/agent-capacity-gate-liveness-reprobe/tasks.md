## 1. Probe-through for cooldown-derived capacity gates

- [ ] 1.1 In `src/worktrail/orchestrator/agent_capacity.py`: add `PROBE_INTERVAL_S` read from
      `GO_AGENT_GATE_PROBE_INTERVAL` (default 900) and `NEVER_PROBE_CLASSES = frozenset({"model_unavailable"})`;
      give `record()` a `reset_source: str = "cooldown"` kwarg stored on the entry; extend
      `check()` so that when the entry is gated (`retry_at > now`), `state.get("reset_source")`
      is not `"provider"`, `failure_class` is not in `NEVER_PROBE_CLASSES`, and
      `now - max(parse(probe_at), parse(checked_at)) >= PROBE_INTERVAL_S`, it takes
      `write_lock(path)`, re-loads, re-verifies the same condition, stamps `probe_at = now.isoformat()`,
      saves, and returns without raising; otherwise raise `ProviderUnavailable` as today. Add a
      `probed:` line to `cmd_status` for an entry carrying `probe_at` (design.md D1, D2).
      In `tests/orchestrator/test_agent_capacity.py`, add: a `billing` gate with `checked_at`
      20 minutes back and no `probe_at` lets the first `check()` through and writes `probe_at`,
      and a second `check()` one second later raises; a gate with `checked_at` 5 minutes back
      raises and leaves the file byte-identical; a `reset_source: provider` gate with old
      `checked_at` raises and writes no `probe_at`; a `model_unavailable` gate with old
      `checked_at` raises and writes no `probe_at`; an entry with no `reset_source` field is
      probeable; `record()` stores `reset_source` and defaults it to `cooldown`; `cmd_status`
      prints `probed:` for an entry with `probe_at`; `GO_AGENT_GATE_PROBE_INTERVAL` overrides
      the cadence (Requirements: A cooldown-derived gate is re-probed on a cadence; An auth
      failure gates its cell without retry).
      files: src/worktrail/orchestrator/agent_capacity.py, tests/orchestrator/test_agent_capacity.py
- [ ] 1.2 In `src/worktrail/orchestrator/spawnlib.py`'s exhausted-budget record path (around
      line 1238), call `agent_capacity.parse_explicit_reset(f"{last_raw}\n{proc.stderr or ''}")`;
      when it returns a timestamp pass it as `retry_after` with `reset_source="provider"`,
      otherwise keep `retry_after=agent_capacity.retry_time(failure_class)` with the default
      `reset_source`. Leave the session-limit `rate_limit` record (line ~1137) and the
      `available` record (line ~1219) unchanged (design.md D3). In
      `tests/orchestrator/test_spawnlib.py`, alongside the existing capacity-record coverage,
      add: a codex cell that exhausts its attempts with output containing "try again at Aug
      8th, 2026 2:17 AM" records a gate whose `retry_after` is that instant and whose
      `reset_source` is `provider`; the same failure without a stated reset records
      `reset_source: cooldown` and the class cooldown; a successful spawn on a cell whose cache
      entry is gated-but-probeable records `available` and a following `check()` passes
      (Requirement: A cooldown-derived gate is re-probed on a cadence). Depends on 1.1.
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py

## 2. Ceiling-exit PR re-check in land_pr

- [x] 2.1 In `src/worktrail/router/land_pr.py`, add a module-level
      `_pr_is_merged(repo, pr_number, runner) -> bool` that runs `gh pr view <n> --json state`
      via `_gh` and returns `True` only when the call succeeds, parses, and `state == "MERGED"`.
      In `land_pr()`'s `if watch["budget_exhausted"]:` branch, before finishing the run record,
      check `pr_number and _pr_is_merged(repo, pr_number, runner)`; when true, replace `watch`
      with the all-pass shape (`settled: True`, empty `failing_checks`/`log_excerpt`,
      `budget_exhausted: False`) and fall through to the existing merge-state guard /
      review-thread gate / `MERGED` completion instead of returning. Leave every other ceiling
      branch and the settled path untouched (design.md D4). In `tests/router/test_land_pr.py`,
      add `_pr_is_merged: False` to `LandPrOrchestrationTests._patched`'s defaults, then add:
      budget exhausted with `_pr_is_merged` returning `True` and `_merge_state_guard` returning
      `{"state": "MERGED"}` yields `outcome == "landed"`, the run-record spy sees `finish
      --status completed_and_merged` with "merged externally", and `_review_thread_gate` was
      called; budget exhausted with `_pr_is_merged` `True` but a blocking review thread yields
      `review_threads_blocking` and no finish; budget exhausted with `_pr_is_merged` `False`
      still finishes `failed_recoverable` with "checks still pending at watch budget"; a direct
      `_pr_is_merged` test showing a non-zero `gh` exit, malformed JSON, and `state: OPEN` each
      return `False` and `MERGED` returns `True` (Requirement: CI watch runs to a classified
      terminal outcome).
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr.py

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass;
      depends on 1.1, 1.2, 2.1. Verification-only, no file changes expected.
