## 1. Routed default spawn in the conductor (requirement: The default compile spawn routes through repo policy with full-real's precedence)

- [x] 1.1 In `src/worktrail/conductor/compile.py`, add a policy-routing resolver helper that takes (repo, explicit agent/model/chain or Nones) and returns `(agent, model, fallback_chain)`: agent via `invocation_context.resolve(agent=..., policy_agent=policy.get("agent_cli")).agent_cli` with the warn-and-claude fallback on `ValueError`; model from the resolved routing entry's `agent_model` else `spawnlib.default_model_for_agent(agent)`; chain from `resolve_routing(policy, route="", risk="")["fallback"]` mapped to a name list. No `os.environ` reads anywhere in the new code.
- [x] 1.2 Extend `_default_spawn` with keyword-only `agent=None, model=None, fallback_agent=None` (positional signature and `cwd` semantics unchanged), resolve via 1.1 against `load_policy(cwd)` when any layer is unset, and pass `agent=`/`model=`/`fallback_agent=` through to `spawnlib.spawn_agent`.
- [x] 1.3 Add `--agent`, `--model`, `--fallback-chain` flags to compile.py's `main()` (default None; chain parsed as an ordered comma-separated name list, names only per design D4) and thread them into `_default_spawn`; log one line recording the resolved agent/model/hop count before spawning.

## 2. Tests (tests/conductor/test_compile.py + tests/orchestrator) — including the no-new-env-channel guards (requirement: No environment-variable channel is added for compile model selection)

- [x] 2.1 Pin the seam: existing `_default_spawn` patch sites still pass unchanged, and an injected `spawn=` callable is used verbatim with no policy load (assert the resolver never runs when `spawn` is provided).
- [x] 2.2 Unconfigured-repo parity: no policy/routing files, no flags → spawn called with claude, config-file default model, no fallback hops (byte-identical argv inputs to pre-change).
- [x] 2.3 Policy primary: repo policy sets `agent_cli`/`agent_model` → those win over defaults; machine-wide routing file variant covered via `WORKTRAIL_ROUTING_FILE` fixture.
- [ ] 2.4 Explicit invocation precedence: `--agent opencode --model openrouter/stealth/ox-alpha` beats configured policy values; partial overrides (flag without model) fall back per-layer.
- [ ] 2.5 Fallback chain threading: `routing.fallback` (and flat `fallback_agent_cli`) reaches `spawn_agent(fallback_agent=[...])`; capacity-gated primary degrades to next hop (patch `agent_capacity.check`); all-hops-gated raises through to the give_up baseline plan with the reason in RunPlan notes.
- [ ] 2.6 Sibling reconciliation guards: ambient `ORCH_OPENCODE_MODEL`/`ORCH_CODEX_MODEL` set in env does not influence the compile spawn's model; policy `agent_model` absent + model-defaults entry present → file value wins.

## 3. Verification

- [ ] 3.1 [e2e] Repo-wide grep confirms zero new `os.environ` reads in `src/worktrail/conductor/compile.py` and no new env-var names introduced anywhere.
- [ ] 3.2 [e2e] `PYTHONPATH=src pytest -q` green, including the golden `worktrail.orchestrator.orchestrate check` replay (`PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`).
