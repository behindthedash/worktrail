## Why

Operator model/provider intent is currently spread across **five** machine-wide surfaces
plus a per-repo block, with no single owner. Verified on this machine 2026-08-26:

| Surface | Read by | State |
|---|---|---|
| `~/.worktrail/config.json` | `shared/operator_config.py` -> drain only | live |
| `~/.worktrail/routing.yaml` | `router/policy.py::_resolve_routing` -> dispatch/live | live |
| `~/.worktrail/provider-model-catalog.yaml` | `runtime/catalog.py` + its tests + a docs example | **read by nothing else** |
| `~/.worktrail/agent-capacity.json` | `orchestrator/agent_capacity.py` | live (evidence, not intent) |
| `~/.worktrail/model-defaults.yaml` | `spawnlib._model_defaults_file()` | **absent** -> hardcoded constants win |
| per-repo `routing:` block | `_resolve_routing` | supported, used by 0 of 15 repos |

`drain.py` synthesizes its own candidate list from `config.json`'s agent names with the
sentinel model `"configured-default"` (drain.py:472-475) before calling
`select_execution_target` -- it never calls `default_catalog()`. Grepping
`default_catalog|load_catalog|catalog_path` across `src/` hits only `runtime/` itself.

**The split has already produced drift, not just redundancy:**

- **Contradictory fallback order.** `config.json` drain declares `opencode` primary with
  fallbacks `[claude, codex]`; `routing.yaml` declares `fallback: [claude, codex, opencode]`.
  The same concept, in two files, in opposite priority order.
- **Three disagreeing model sets.** The catalog knows codex as `gpt-5.4-mini` (one model);
  `routing.yaml` dispatches `gpt-5.6-sol`/`-terra`/`-luna` (three, none of them that one).
  The catalog carries no `opus` entry while `routing.roles.review` pins the entire review
  role to it. The catalog names opencode `opencode/deepseek-v4-flash-free`; routing was
  swapped to `opencode/x-preview-f-free` on 2026-08-25 and the catalog was never updated.
- **Stale intent leaks into observability.** `agent-capacity.json`'s hand-maintained
  `configured_providers` is exactly the catalog's stale trio, while `providers` holds 17
  records. `gate_snapshot()` computes `all_gated` over only those three, so the dashboard
  and run records report gating against a provider set dispatch does not use.
- **A hardcoded constant is the live value.** `spawnlib.py:376` pins
  `DEFAULT_CODEX_MODEL = "gpt-5.4-mini"` with a comment admitting it drifted before, and
  `model-defaults.yaml` -- the file that exists to fix that without a code change -- was
  never created on this machine.
- **Triplicated literal.** `opencode/x-preview-f-free` plus an identical two-line comment
  appears three times in `routing.yaml` (t2/t3/t4). One model swap is three edits, and the
  catalog is a silent fourth that never gets made.

Every one of these is the same root cause: **operator intent has no single home.**

## What Changes

Collapse to **two machine-wide files with one job each**:

- **`~/.worktrail/routing.yaml` -- all operator intent.** Absorbs the drain preferences
  from `config.json`, the per-agent default models from `model-defaults.yaml`, and gains a
  nested tier table plus an `agents:` block that names each provider's models once.
- **`~/.worktrail/agent-capacity.json` -- all runtime evidence.** Unchanged in role, minus
  the hand-maintained `configured_providers` key, which becomes derived from routing.

Concretely:

- **Delete `src/worktrail/runtime/catalog.py`** (~330 LOC) and `tests/test_runtime_catalog.py`,
  along with `provider-model-catalog.yaml`, its `.example`, and `WORKTRAIL_PROVIDER_MODEL_CATALOG_FILE`.
  `runtime/selection.py` is **kept and unchanged** -- `select_execution_target` is already
  duck-typed (`_catalog_items()` accepts a plain `{provider: [models]}` mapping; `_policy_values()`
  already reads `purpose_tiers`/`tiers`/`defaults`/`fallbacks`/`fallback_chain`, which is
  routing.yaml's own shape), so it consumes routing directly via a thin adapter.
- **Delete `shared/operator_config.py` and `config.json`.** Drain reads `routing.drain`.
- **Delete `model-defaults.yaml`, `MODEL_DEFAULTS_FILE_ENV`, and the hardcoded
  `DEFAULT_CLAUDE_MODEL`/`DEFAULT_CODEX_MODEL`/`DEFAULT_OPENCODE_MODEL` constants.**
  `default_model_for_agent()` resolves from `routing.agents.<agent>.default_model` and
  **fails loud** when routing does not declare one. No env var carries a model value
  (extending, not contradicting, the merged `model-tier-routing-remove-env-model-overrides`).
- **Nest the tier table.** `tiers.<tier>.<agent>` replaces the twelve flat
  `<tier>-<agent>` keys; the flat form still loads for one release with a deprecation warning.
- **Derive `configured_providers`.** `gate_snapshot()` takes its provider set from resolved
  routing instead of a stored key, so "all gated" is always measured against what dispatch
  actually uses.
- **Correct the fallback contradiction** in the operator's own file: claude-first
  (`routing.yaml`'s order) is the intended precedence; the drain's opencode-first preference
  was the accidental one.
- **Keep the per-repo `routing:` override.** No repo uses it today, but it costs nothing to
  retain and `_resolve_routing`'s precedence chain is unchanged.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-tier-routing`: default model resolution moves from a separate config file plus
  hardcoded constants to `routing.agents.<agent>.default_model`, failing loud when absent;
  the tier table gains a nested form; the provider/model registry is routing, not a catalog.
- `drain-operator-config`: the operator config file becomes `routing.yaml`'s `drain:` block
  rather than `config.json`; CLI-over-config-over-built-in precedence is preserved, except
  that there is no longer a built-in agent default to fall through to.

### Removed Capabilities

(none -- no capability spec ever covered `runtime/catalog.py`; PRs #730/#731 landed it
without an OpenSpec change.)

## Impact

- **Deleted:** `src/worktrail/runtime/catalog.py`, `src/worktrail/shared/operator_config.py`,
  `tests/test_runtime_catalog.py`, `tests/shared/test_operator_config.py`,
  `docs/config/provider-model-catalog.yaml.example`.
- **`src/worktrail/runtime/selection.py`**: unchanged; gains a `routing_candidates()` adapter
  as a sibling (either in `selection.py` or a new small `runtime/routing_source.py`).
- **`src/worktrail/router/policy.py`**: `_validate_routing_tiers` accepts the nested form;
  new `agents:` and `drain:` block validation; `resolve_tier_map` reads nested keys.
- **`src/worktrail/orchestrator/spawnlib.py`**: constants and `MODEL_DEFAULTS_FILE_ENV`
  removed; `default_model_for_agent()` resolves from routing and raises when unset.
- **`src/worktrail/orchestrator/agent_capacity.py`**: `configure()` removed or reduced;
  `gate_snapshot()` takes an explicit provider set.
- **`src/worktrail/drain/drain.py`**: reads `routing.drain`; feeds real models to
  `select_execution_target` instead of the `"configured-default"` sentinel.
- **`src/worktrail/onboarding/repo_init.py`** (or a new `worktrail-routing --init`): writes a
  starter `routing.yaml`, since a machine without one can no longer spawn anything.
- **Operator migration** (this machine): fold `config.json` into `routing.yaml` with
  claude-first order, add the `agents:` block, nest the tiers, delete the three dead files,
  and remove the stray `~/.worktrail/runs/agent-capacity.json`.
- **Sequencing:** lands after the in-flight
  `model-tier-routing-compile-default-spawn-policy-routing` (3/11 tasks done), whose spec
  text names `model-defaults.yaml`; this change updates that reference.
