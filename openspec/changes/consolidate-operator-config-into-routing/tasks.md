# Tasks

## 1. routing.yaml schema: `agents:` and `drain:` blocks

- [x] 1.1 Add `_validate_routing_agents()` to `router/policy.py`: `agents: {<agent>:
      {default_model: str}}`, warning-and-dropping malformed entries via the existing
      `meta["warnings"]` channel, consistent with the sibling `_validate_routing_*` validators.
- [x] 1.2 Add `_validate_routing_drain()`: `drain: {agent: str, fallback_agents: [str],
      max_workers: int>=1}`, porting the shape checks from `shared/operator_config.py::drain_config`
      including its loud-failure semantics for a malformed section.
- [x] 1.3 Expose both through `resolve_routing()`'s returned dict (`agents`, `drain` keys) and
      extend its docstring contract; keep the function pure (no I/O, no clock).
- [x] 1.4 Tests: valid blocks resolve; malformed blocks warn without crashing `load_policy`;
      absent blocks resolve to `{}` and change nothing.

## 2. Nested tier table (D6)

- [x] 2.1 Implements **The tier table supports a nested per-agent form**: extend
      `_validate_routing_tiers()` to accept `tiers.<tier>.<agent>: {model, effort}`
      alongside today's flat `<tier>-<agent>` keys.
- [x] 2.2 Emit a deprecation warning through `meta["warnings"]` when the flat form is used.
- [x] 2.3 Update `resolve_tier_map()` so `dispatch.agent_for()`'s `<tier>-<agent>` lookup
      resolves identically from either shape -- `dispatch.py` itself is not modified.
- [x] 2.4 Tests: nested and flat forms produce byte-identical `resolve_tier_map()` output;
      nested wins when both declare the same tier/agent.

## 3. Default model resolution from routing (D2, D3)

- [x] 3.1 Narrows **Default model resolution is config-file driven only** to a single
      source: delete `DEFAULT_CLAUDE_MODEL`, `DEFAULT_CODEX_MODEL`, `DEFAULT_OPENCODE_MODEL`,
      `MODEL_DEFAULTS_FILE_ENV`, `_model_defaults_file()`, and `_load_model_defaults()` from
      `orchestrator/spawnlib.py`.
- [x] 3.2 Implements **Per-agent default models resolve from routing only**: reimplement
      `default_model_for_agent(agent)` against `routing.agents.<agent>.default_model`.
- [x] 3.3 Raise a named, actionable error (naming the routing file path, the missing
      `agents.<agent>.default_model` key, and `worktrail-routing --init`) when routing declares
      no default for the requested agent. No silent fallback.
- [x] 3.4 Update `tests/orchestrator/test_spawnlib.py` and `tests/conftest.py` fixtures that
      currently seed `model-defaults.yaml`.
- [x] 3.5 [cleanup] Grep-verify no model string remains hardcoded in `src/` outside test fixtures:
      `rg -n "gpt-5\.|sonnet|opus|haiku|deepseek|x-preview" src/` reviewed hit by hit. (one
      pre-existing, out-of-scope hit noted: `orchestrator/verify.py:124`'s own hardcoded
      `DEFAULT_MODEL = "sonnet"` ci-fix fallback -- not named in this change's tasks, left as-is.)

## 4. Drain reads routing (D1) and selects real models (D4)

- [x] 4.1 Replace `drain.py`'s `operator_drain_config()` import with `resolve_routing()`'s
      `drain` block; preserve CLI > config > built-in precedence and the exit-2 refusal on an
      unsupported agent name, now naming `routing.yaml`.
- [ ] 4.2 Add `routing_candidates(routing)` (in `runtime/selection.py` or a new
      `runtime/routing_source.py`) yielding `{provider, model, tiers, purposes}` from
      `routing.agents` + `routing.tiers`.
- [ ] 4.3 Implements **Provider/model intent has exactly one machine-wide file**: replace
      drain.py:472-475's synthetic `"configured-default"` sentinel catalog with
      `routing_candidates(...)`, so capacity gating keys on the real model actually spawned.
      `select_execution_target` itself is NOT modified.
- [x] 4.4 Delete `src/worktrail/shared/operator_config.py` and `tests/shared/test_operator_config.py`.
- [ ] 4.5 Tests: drain honors `routing.drain`; a per-model gate no longer gates a whole
      provider; CLI flags still win outright.

## 5. Fail-closed mitigation -- ship with D3, not after it

- [ ] 5.1 Add `worktrail-routing` console script with `--init` (writes a starter `routing.yaml`
      covering `agents`, `fallback`, `roles`, `purpose_tiers`, `tiers`, `drain`) and `--show`
      (prints the resolved routing for a repo).
- [ ] 5.2 Wire `--init` into `onboarding/repo_init.py` so a bootstrapped machine is never left
      unable to spawn.
- [ ] 5.3 Acceptance test: a fresh `WORKTRAIL_HOME` with no `routing.yaml` produces the
      actionable named error (not a traceback, not a silent paid-model spawn), and
      `worktrail-routing --init` then makes the same spawn resolve.

## 6. Delete the catalog (D4)

- [ ] 6.1 Delete `src/worktrail/runtime/catalog.py`, `tests/test_runtime_catalog.py`,
      `docs/config/provider-model-catalog.yaml.example`, and the catalog exports from
      `src/worktrail/runtime/__init__.py`. Confirm `runtime/selection.py` and
      `tests/test_runtime_selection.py` are untouched.
- [ ] 6.2 Update the in-flight `model-tier-routing-compile-default-spawn-policy-routing`
      change's proposal/spec references from `model-defaults.yaml` to
      `routing.agents.<agent>.default_model`.
- [ ] 6.3 [cleanup] `rg -n "provider-model-catalog|ModelCatalog|default_catalog|runtime.catalog" .`
      from the repo root, no path or file-type filter -- reconcile every hit including
      `.github/`, `scripts/`, `skills/`, and docs.

## 7. Derived `configured_providers` (D5)

- [x] 7.1 Change `agent_capacity.gate_snapshot()` to take an explicit provider set instead of
      reading a stored `configured_providers` key.
- [ ] 7.2 Remove `agent_capacity.configure()` and its call sites, or reduce it to a no-op
      shim if a caller outside this change still needs it (verify with a repo-root grep).
- [x] 7.3 Update the dashboard and run-record call sites to pass the routing-derived set.
- [ ] 7.4 Tests: `all_gated` is computed over the routing-derived set; a stale
      `configured_providers` key left in an existing cache file is ignored, not honored.

## 8. Operator migration (this machine)

- [ ] 8.1 [cleanup] Rewrite `~/.worktrail/routing.yaml`: add `agents:` (declaring each provider's default
      model once), nest `tiers`, fold in `drain:` with **claude-first** order per the
      2026-08-26 decision, and keep the existing pricing/free-tier commentary.
- [ ] 8.2 [cleanup] Delete `~/.worktrail/config.json` and `~/.worktrail/provider-model-catalog.yaml`.
- [ ] 8.3 [cleanup] Remove the stale `configured_providers` key from `~/.worktrail/agent-capacity.json`.
- [ ] 8.4 [cleanup] Investigate and remove the stray `~/.worktrail/runs/agent-capacity.json` -- confirm
      what wrote it (suspected relative `WORKTRAIL_HOME` or cache override) before deleting,
      and fix that path resolution if the cause is in worktrail.

## 9. Docs

- [ ] 9.1 Update `AGENTS.md` and any skill `references/` naming `config.json`,
      `model-defaults.yaml`, or the catalog.
- [x] 9.2 Add a `docs/config/routing.yaml.example` covering the full consolidated schema,
      replacing the deleted catalog example.
