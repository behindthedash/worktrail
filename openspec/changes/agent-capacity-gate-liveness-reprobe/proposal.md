## Why

`src/worktrail/orchestrator/agent_capacity.py`'s `check()` (lines ~193-205) and every caller
built on it (`gate_for_agent()`, `runtime/selection.py`'s `select_cell` via
`router/skill_dispatch.py:421`, `spawnlib.spawn_agent`'s cell selection) gate purely on the
persisted `retry_after`/`reset_at` being in the future. There is no provider probe of any kind
(`grep -n 'liveness\|probe' agent_capacity.py runtime/selection.py` returns nothing), so once
a gate is written it stays authoritative until its window expires or an operator runs
`worktrail-agent-capacity clear`. For a `billing` gate the window is a *guessed* one-hour
cooldown (`DEFAULT_COOLDOWNS["billing"] = 3600`): `spawnlib.py:1238` records it with
`retry_time(failure_class)` and never consults `parse_explicit_reset()`, even though that
helper exists in the same module and `drain.py:2608` already uses it. `auth` is a guessed
24-hour window. Both describe outages that routinely lift well before the guess expires.

Live evidence from work-queue brief `20260905-195249-capacity-cache-honors-stale-billing`: on
2026-09-06 a `claude-sub:fable` billing gate set at 01:37Z (retry 02:37Z) blocked a
`worktrail-go` triage dispatch entirely while the operator confirmed claude had capacity. The
cache's own audit log shows four prior manual clears for the same stale-gate pattern
(2026-08-11, 2026-08-22 x3, 2026-08-25). PR #1005 added `clear --expired` and PR #1006 bounded
session-limit parks to a re-probe cadence, but neither makes an *active* gate self-heal: a
lifted gate still needs a human.

Secondary, same area: `src/worktrail/router/land_pr.py`'s watch-budget ceiling (line ~1160)
finishes the run record as `failed_recoverable` ("checks still pending at watch budget") without
re-querying the PR. On PR ggb#743 auto-merge completed six minutes after the ceiling fired, so
a run whose PR actually landed is recorded as a recoverable failure that then needs manual
reconciliation. The all-pass path already re-queries the live PR and finishes as merged when it
finds `state == MERGED`; the ceiling path does not.

No active change under `openspec/changes/` covers gate liveness or a ceiling-exit PR re-check
(checked via `ls openspec/changes`).

## What Changes

- **Probe-through for stale gates.** `agent_capacity.check()` gains a bounded re-probe: when a
  gate is still inside its retry window but has not been probed within the probe cadence, one
  caller is let through (the probe is claimed under `write_lock` by stamping `probe_at` on the
  entry, so concurrent workers still see the gate). The spawn that caller performs *is* the
  liveness probe: `spawn_agent` already records `outcome="available"` on success
  (`spawnlib.py:1219`), which clears the gate, and re-records the gate with a fresh window on
  failure. No separate provider ping is introduced.
- **Provider-reported resets are authoritative; guessed cooldowns are probeable.** `record()`
  gains a `reset_source` field (`"cooldown"` by default, `"provider"` when the retry window came
  from the provider's own notice). A `provider` gate is never probed; a `cooldown` gate (and any
  pre-existing entry with no `reset_source`) is. `model_unavailable` is never probed regardless,
  since a retired model does not come back by waiting.
- **`spawnlib` records the provider's stated reset for billing gates.** The infra-failure record
  path (`spawnlib.py:1238`) now tries `parse_explicit_reset()` on the captured output before
  falling back to `retry_time()`, and marks the result `reset_source="provider"` when it hit. A
  multi-day Codex usage cap is therefore gated to its real reset and not probed every cadence.
- **`status` shows the last probe.** `worktrail-agent-capacity status` prints a `probed:` line for
  an entry that carries `probe_at`, so an operator can see the gate is being re-checked.
- **Ceiling-exit PR re-check in `land_pr`.** On watch-budget exhaustion, before finishing the run
  record as `failed_recoverable`, the pipeline re-queries the PR's state once. If it is already
  merged, the pipeline continues into the existing all-pass completion flow (merge-state guard,
  review-thread gate, finish as merged externally) instead of recording a failure.

## Capabilities

### Modified Capabilities

- `model-tier-routing`: adds the probe-through requirement for cooldown-derived gates and amends
  "An auth failure gates its cell without retry" so an auth gate also lifts on a successful probe,
  not only on expiry or a manual clear.
- `pr-landing-pipeline`: amends "CI watch runs to a classified terminal outcome" so the
  watch-budget-exhausted outcome re-queries the PR and finishes as merged when it already is.

## Impact

- **Code**: `src/worktrail/orchestrator/agent_capacity.py` (`record`, `check`, `cmd_status`, new
  probe-cadence constant), `src/worktrail/orchestrator/spawnlib.py` (billing record path),
  `src/worktrail/router/land_pr.py` (watch-budget ceiling branch, one new `gh pr view` helper).
- **Tests**: `tests/orchestrator/test_agent_capacity.py`, `tests/orchestrator/test_spawnlib.py`,
  `tests/router/test_land_pr.py`.
- **Operator surface**: one new env knob `GO_AGENT_GATE_PROBE_INTERVAL` (seconds, default 900,
  matching `ORCH_SESSION_LIMIT_REPROBE_MAX_S`'s cadence); one new `probed:` line in `status`
  output. `clear` semantics unchanged.
- **Non-goals**: a standalone provider ping command; probing `drain.py`'s bare-agent gates (drain
  has its own `capacity_gated()` reader and its own iteration cadence -- untouched); changing
  `gate_snapshot()`'s dashboard reporting (a probed gate is still an active gate for display);
  re-checking PR state on any ceiling other than watch-budget exhaustion (the push-ambiguous,
  PR-update, run-record, and code-defect-iteration ceilings describe states where a merge is not
  the plausible resolution); changing `DEFAULT_COOLDOWNS`.
