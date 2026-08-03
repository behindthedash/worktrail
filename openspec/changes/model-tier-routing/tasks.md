## 1. Pre-implementation verification (do first — later tasks depend on the answers)

- [x] 1.1 Confirm codex's full accepted `model_reasoning_effort` value set (only `"low"`
      observed live in the operator's `~/.codex/config.toml`) against `codex exec
      --help` / official Codex docs
      **Finding (2026-08-03):** `codex exec --help` documents no explicit enum for
      `-c model_reasoning_effort=`; the canonical value set is defined in the Codex
      Rust source, `codex-rs/protocol/src/openai_models.rs`, `enum ReasoningEffort`:
      `minimal | low | medium | high | xhigh`. The enum also carries a `Custom(String)`
      fallback variant that accepts *any* other string with zero client-side
      validation — confirmed live: `codex exec -c model_reasoning_effort=bogus ...`
      was accepted and echoed in the startup banner (`reasoning effort: bogus`) with
      no parse error; the run only failed downstream on an unrelated usage-limit
      error, never on the effort value itself. Task 3.1's codex translation should
      pass through the canonical 5 values unchanged and NOT attempt client-side
      validation beyond that (matching codex's own permissive `FromStr`).
- [x] 1.2 Live-verify opencode's `--variant` support for `deepseek-v4-flash`
      specifically: run `opencode run --model opencode/deepseek-v4-flash --variant
      <value> "<trivial prompt>"` for each of `high`, `max`, `minimal` (the CLI's own
      documented examples) and record which are accepted vs. rejected vs. silently
      ignored, and whether any observable behavior change confirms the flag actually
      took effect
      **Finding (2026-08-03):** All of `high`, `max`, `minimal`, and a deliberately
      invalid probe value (`bogus-invalid-xyz`) were accepted identically — opencode
      1.17.13's CLI performs no client-side validation of `--variant` for this model.
      `--log-level DEBUG` tracing of the actual chat request (the `llm runtime
      selected` event with `llm.provider=opencode llm.model=deepseek-v4-flash` —
      distinct from an earlier internal `gpt-5.4-nano` call used for session/title
      bookkeeping) shows no mention of "variant" anywhere in the pipeline from CLI
      parse through provider dispatch, for any of the four values tested. Verdict:
      **silently ignored** for `deepseek-v4-flash` — no observable evidence the flag
      reaches the provider or changes behavior. Task 3.1's opencode translation
      should still emit `--variant` when an effort is configured (forward-compatible
      if opencode adds real support later, and it is the CLI's own documented flag
      name) but must not assume it has any effect on this specific model today, and
      the 3-tier fallback example (task 4.2) should not claim a verified behavior
      change for `deepseek-v4-flash` specifically.
- [x] 1.3 Decide the tier-key naming convention (`T1`/`T2`/`T3`/`T4` literal vs. a
      lowercase/hyphenated form) for consistency with existing `trivial`/`standard`/
      `hard` complexity values
      **Decision (2026-08-03):** Lowercase-hyphenated form — `t1-deep`, `t2-build`,
      `t3-bulk`, `t4-trivia` — matching design.md's own suggested example ("a
      repo/operator-preferred lowercase like `t1-deep`") and the existing
      lowercase-word convention already used by `trivial`/`standard`/`hard`.

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
- [ ] 4.3 [e2e] Regression test: full suite + golden check (`orchestrate check`) green with
      these entries populated, confirming no interaction with existing
      `dispatch.agent_for()` precedence rules for `JUDGMENT_ROLES`

## 5. Explicitly deferred (do not attempt in this change)

- [ ] 5.1 Write a follow-up proposal for task-purpose classification (architecture
      design vs. terminal-heavy automation vs. security review vs. CRUD scaffold,
      etc.) — this change's tiers are reachable manually/via existing `complexity`
      frontmatter only, not automatically from task purpose
- [ ] 5.2 Once 5.1 exists, resolve D3 (agent-preference-within-tier: options (a)/(b)/(c)
      in design.md) informed by however classification actually surfaces its signal
