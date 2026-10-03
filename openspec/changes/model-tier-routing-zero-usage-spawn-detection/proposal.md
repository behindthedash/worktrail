## Why

A `claude` worker spawned without a reachable provider environment can make **zero API calls**,
produce no output, and still exit 0. Its stream-json `result` event reads `duration_api_ms: 0`,
`num_turns: 0`, and all-zero token counts — a shape `spawnlib.is_infra_failure()` does not
recognize (reproduced directly: it returns `False`; `_parse_stream_json` does not even retain
`duration_api_ms`). `spawn_agent` therefore records the cell `available` and hands the empty
output back as a *successful* run, so an orchestrator group counts a worker that never ran as
completed. The operator's own routing file (`~/.worktrail/routing.yaml`, 2026-10-02) carries the
workaround this defect forced — an `env_profiles` entry whose `expect` refuses to launch when
the endpoint is missing — which fixes one cause of the shape but not the class: any future cause
(pool branch, profile drift, settings bug) still exits 0 silently.

The same spawn layer carries two related defects. `run_research_session`'s `--fork-research`
pre-load and `smoke()` still call `spawn_agent` with `agent=`/`model=`/`effort=` kwargs its
tier-based signature no longer accepts, so any real invocation raises
`TypeError: unexpected keyword argument 'agent'` — both reproduced — while their tests stub the
callee, so the suite cannot see it. And `worktrail-live precheck`, which spawns nothing, dies
before its DAG check on any routing table with no target for the invocation host's harness
(reproduced: `OperatorConfigError: no default model configured for agent 'claude'`), because
`main()`'s shared tail resolves a worker model for every subcommand unconditionally.

## What Changes

- **Detect the no-op spawn and classify it as an infra failure.** `spawnlib`'s result
  classification (the `is_infra_failure` predicate) treats a claude result event showing zero
  API calls — `duration_api_ms: 0`, `num_turns: 0`, every token count 0 — as an infra failure,
  so the existing retry-then-hop path runs before any capacity gate is recorded.
  `_parse_stream_json` starts retaining `duration_api_ms` from the result event. The predicate
  must not fire on a legitimate completed turn (any `num_turns >= 1` / non-zero `input_tokens`).
- **Map the exhausted no-op to a short-cooldown failure class.** When the shape survives the
  cell's whole retry budget, the gate is recorded with the existing `startup` class (60 s
  default) — never `auth` (24 h, gates without retry) or `model_unavailable` (24 h, never
  probed). No existing failure-class cooldown value changes.
- **Repair the two `live.py` call sites** to `spawn_agent`'s real signature, resolving the
  launch cell through `tier`/`prefer` — the routing file's `default_tier` row with the
  requested harness expressed as a target preference — the way the working call sites do.
- **Make `worktrail-live precheck` runnable without a worker model**: `main()`'s tail resolves
  the worker model (and the codex role-model defaults) only for subcommands that actually
  spawn; non-spawning subcommands (`precheck`, `status`, `usage`, `skip`, `clear-task`,
  `instantiate`) no longer require a routing table that serves the invocation host.
- **Regression tests that fail against the current code**: the no-op result event pinned to
  `is_infra_failure(...) == True` with a real-completed-turn contrast; both repaired call sites
  exercised through the real `spawn_agent` signature (not a MagicMock); `precheck` running its
  DAG check green on a routing table with no host-harness target, with a spawning subcommand
  still failing loud on the same table.

## Capabilities

### New Capabilities

<!-- none: this change fixes behavior under an existing capability -->

### Modified Capabilities

- `model-tier-routing`: a zero-API-call result event is classified as an infra failure (not a
  successful-but-empty run), and its exhausted gate maps to a short-cooldown infra class; the
  spawn layer's retry/hop semantics are otherwise unchanged.

## Impact

- **`src/worktrail/orchestrator/spawnlib.py`** — `_parse_stream_json` (retain `duration_api_ms`),
  `is_infra_failure` (no-op clause), `spawn_agent`'s exhausted-budget class resolution
  (`startup` mapping). `is_infra_failure`'s two other callers are analyzed in the design:
  `check_agent_contract.py` (a no-op now fails the claude contract check with a truer message —
  same verdict) and `codex_probe.py` (reads codex's own event vocabulary; unaffected).
- **`src/worktrail/orchestrator/live.py`** — `run_research_session`, `smoke`, a shared
  harness→target lookup used by `LiveSpawn.__call__`, and `main()`'s worker-model tail.
- **Tests** — `tests/orchestrator/test_spawnlib.py`, `tests/orchestrator/test_live_extras.py`,
  `tests/orchestrator/test_precheck.py`.
- **Not touched**: routing cassettes / route classification, any `DEFAULT_COOLDOWNS` value, the
  `tests/orchestrator/test_spawn_exhausted_callers.py` EXEMPT keys (both repaired calls stay in
  their current functions), release metadata (no version bump in this PR).
