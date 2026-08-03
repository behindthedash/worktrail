## 1. Pre-implementation verification (do first — later tasks depend on the answers)

- [ ] 1.1 Confirm codex's full accepted `model_reasoning_effort` value set (only `"low"`
      observed live in the operator's `~/.codex/config.toml`) against `codex exec
      --help` / official Codex docs
- [ ] 1.2 Live-verify opencode's `--variant` support for `deepseek-v4-flash`
      specifically: run `opencode run --model opencode/deepseek-v4-flash --variant
      <value> "<trivial prompt>"` for each of `high`, `max`, `minimal` (the CLI's own
      documented examples) and record which are accepted vs. rejected vs. silently
      ignored, and whether any observable behavior change confirms the flag actually
      took effect
- [ ] 1.3 Decide the tier-key naming convention (`T1`/`T2`/`T3`/`T4` literal vs. a
      lowercase/hyphenated form) for consistency with existing `trivial`/`standard`/
      `hard` complexity values

## 2. Schema: effort field on agent-entry

- [ ] 2.1 Add optional `effort: Optional[str]` to `policy._validate_agent_entry()`
- [ ] 2.2 Thread `effort` through `resolve_routing()`'s returned `fallback`/`roles`/
      `tiers` entry shape
- [ ] 2.3 Unit tests: entry with `effort` validates and resolves; entry without
      `effort` resolves with `effort: None`; invalid (non-string) `effort` is dropped
      with a warning, matching the existing `_validate_agent_entry` pattern for
      malformed `agent_model`

## 3. Plumbing: effort reaches the spawned CLI

- [ ] 3.1 Add `effort: Optional[str] = None` parameter to `spawnlib.build_cmd()`;
      translate to `--effort` (claude) / `-c model_reasoning_effort=` (codex) /
      `--variant` (opencode) — only for the values confirmed in 1.1/1.2
- [ ] 3.2 Thread `effort` through `dispatch.agent_for()`'s resolved
      `{"agent_cli", "agent_model"}` result (becomes `{"agent_cli", "agent_model",
      "effort"}`)
- [ ] 3.3 Thread `effort` through `live.py`'s `LiveSpawn`, `_effective_role_models()`,
      and the existing `model = model or spawnlib.default_model_for_agent(agent)`
      call sites that need the equivalent for effort
- [ ] 3.4 Unit tests: `build_cmd()` output per agent with/without `effort`
      (byte-identical to pre-change output when `effort` is `None`); an
      end-to-end `LiveSpawn`/`dispatch.agent_for()` test confirming a configured
      tier's `effort` reaches the spawned command

## 4. Review role + 3-tier complexity fallback (ship independently, no classification needed)

- [ ] 4.1 Document (README/skill doc) how to set `routing.roles: {review: {agent_cli:
      claude, agent_model: opus}}` in `~/.go/routing.yaml`
- [ ] 4.2 Document the 3-tier complexity fallback
      (`trivial`/`standard`/`hard` → `gpt-5.6-luna`/`gpt-5.6-terra`/`gpt-5.6-sol`, or
      whatever values 1.1 confirms) as a `routing.tiers` example
- [ ] 4.3 Regression test: full suite + golden check (`orchestrate check`) green with
      these entries populated, confirming no interaction with existing
      `dispatch.agent_for()` precedence rules for `JUDGMENT_ROLES`

## 5. Explicitly deferred (do not attempt in this change)

- [ ] 5.1 Write a follow-up proposal for task-purpose classification (architecture
      design vs. terminal-heavy automation vs. security review vs. CRUD scaffold,
      etc.) — this change's tiers are reachable manually/via existing `complexity`
      frontmatter only, not automatically from task purpose
- [ ] 5.2 Once 5.1 exists, resolve D3 (agent-preference-within-tier: options (a)/(b)/(c)
      in design.md) informed by however classification actually surfaces its signal
