## Context

Model selection currently has two independent layers:

1. **Generic per-agent fallback** — `spawnlib.default_model_for_agent()`. Resolves, in
   order: an explicit `ORCH_*_MODEL` env var → the operator-maintained
   `~/.go/model-defaults.yaml` (PR #114) → a hardcoded constant. No task-awareness at
   all — every dispatch to a given agent gets the same model unless something more
   specific overrides it.
2. **Task-aware routing** — `policy.py`'s `routing.tiers`/`routing.roles`, resolved by
   `resolve_routing()` and consumed by `dispatch.agent_for()`. This mechanism already
   exists and is already wired into live dispatch, but has never been populated by this
   operator — `routing.tiers` is keyed by a task's own `(complexity, domain)`
   frontmatter, and `routing.roles` by role name (`implement`/`review`/`fix`/...).

Neither layer controls **reasoning effort**, confirmed live 2026-08-03 by reading
`build_cmd()` directly: it passes `--model <model>` for all three agents and nothing
else model/reasoning-related. Each agent's actual CLI *does* expose a reasoning-effort
control, confirmed live the same day:

| Agent | Flag | Confirmed values |
|---|---|---|
| claude | `--effort <level>` | `low, medium, high, xhigh, max` (from `claude --help`) |
| codex | `-c model_reasoning_effort=<value>` | operator's own `~/.codex/config.toml` has `model_reasoning_effort = "low"` live today; full value set not enumerated anywhere seen — verify against `codex exec --help`/docs during implementation |
| opencode | `--variant <value>` | flag exists (`opencode run --help`: "model variant (provider-specific reasoning effort, e.g., high, max, minimal)"); **note the CLI's own example values are high/max/minimal, not low/medium/high** |

The operator runs a manual four-tier policy today (T1 Deep, T2 Build, T3 Bulk, T4
Trivia) mapping task purpose to a model+effort choice per agent, plus a task→tier
table with real exceptions and three governing rules. This design captures that policy
as the target behavior and works out how to wire it into the two existing layers above
without duplicating information between them (the operating rule from tonight's
discussion: `model-defaults.yaml` holds the generic baseline; `routing.tiers`/`roles`
entries should only encode what's *different* from that baseline).

## Goals / Non-Goals

**Goals:**
- Represent the operator's T1–T4 tier scheme (model + effort per agent, per tier) in
  `routing.yaml`/`go-policy.yaml`'s existing schema, extended with an `effort` field.
- Thread a resolved `effort` value from `dispatch.agent_for()` through to `build_cmd()`
  and translate it into the correct per-agent CLI flag.
- Pin the `review` role to `claude:opus` (a `routing.roles` entry — no new mechanism
  needed, ships as part of this change).
- Ship a 3-tier complexity fallback (trivial/standard/hard) as an independently useful,
  smaller slice, in case the full T1–T4/task-purpose-classification piece takes longer.
- Preserve exact current behavior for every repo/task that configures no tier/effort
  (hard requirement, mirrors PR #114's model-defaults.yaml precedent).

**Non-Goals (this change does not implement):**
- Automatic task-purpose classification (tagging a task as "security review" vs. "CRUD
  scaffold" vs. "terminal-heavy agentic loop", etc.). Nothing today produces this
  signal; inventing the mechanism is a separate design decision (see Open Questions).
  Without it, the T1–T4 scheme's *task→tier table* cannot be applied automatically —
  only the tier *definitions themselves* (model+effort per tier) can ship now, manually
  selectable via `routing.roles`/an explicit per-task override, until classification
  exists.
- Confirming which effort/variant values `opencode`'s `deepseek-v4-flash` actually
  honors. The `--variant` flag exists generically; this specific model's support is
  unverified (see Open Questions) — until verified, opencode tier entries should either
  omit `effort` (falling back to today's plain `--model` behavior) or be marked
  experimental.
- Any change to `spawnlib.default_model_for_agent()`'s existing precedence (env var >
  model-defaults.yaml > hardcoded) — that mechanism is untouched; this change adds a
  *higher-precedence, task-aware* layer above it (existing `dispatch.agent_for()`
  precedence rules already put role/tier resolution ahead of the generic default).

## Decisions

### D1: `effort` is a new optional field on `_validate_agent_entry`, not a new top-level key

`policy._validate_agent_entry()` validates `{agent_cli, agent_model}` (or `api_opt_in`
for API/OpenRouter entries) today. Add `effort: Optional[str]`, validated as a plain
string (accepted-value enumeration is deliberately *not* enforced here — the valid set
differs per agent, per the confirmed-flags table above, and enforcing a wrong/stale
enum would itself become the kind of staleness bug PR #114 just fixed). An entry with
no `effort` key behaves exactly as today.

**Alternative considered**: a separate `routing.effort` top-level table keyed
independently of `agent_cli`/`agent_model`. Rejected — effort only makes sense paired
with a specific agent (the same nominal effort level maps to different flags/values per
agent), so keeping it on the same entry as the agent/model choice avoids a second
lookup and a second place these three things could disagree.

### D2: Tier definitions live in `routing.tiers`, keyed by tier name as the "complexity" slot

`_validate_routing_tiers`' key format is `<complexity>[/<domain>]`. Use the tier name
itself as the complexity slot: `tiers: {T1: {...}, T2: {...}, T3: {...}, T4: {...}}` (or
a repo/operator-preferred lowercase like `t1-deep` if the literal `T1` reads oddly next
to existing `trivial`/`standard`/`hard` complexity values elsewhere — pick one
convention at implementation time and use it consistently). This reuses the existing,
tested tier-resolution code path (`resolve_tier_map()` → `dispatch.agent_for()`'s
`tier_map` parameter) rather than inventing a parallel one.

**The 3-tier complexity fallback** (trivial/standard/hard) is a *separate, coexisting*
set of tier keys in the same `tiers` block, not a replacement — a task tagged with
`complexity: hard` (today's existing frontmatter convention) resolves via that entry;
a task explicitly tagged (once classification exists) with a T1–T4 tier name resolves
via the corresponding T-tier entry. Until task-purpose classification exists, only the
plain complexity fallback is reachable automatically; the T1–T4 entries are reachable
only via an explicit per-task `--tier`-style override or manual `role_agent_map`-style
invocation.

### D3: The "prefer Claude vs. prefer Codex" per-task-type nuance is NOT solved by a tier entry alone

A single tier entry hardcodes one `agent_cli`+`agent_model`+`effort` triple — it cannot
express "if the fallback chain already selected codex, use gpt-5.6-sol/high; if it
selected claude, use opus/xhigh instead." Resolving this needs one of:
- (a) Two tiers per T-level, one per preferred agent (`T1-claude`, `T1-codex`), selected
  by whatever already decided which agent is live for this dispatch — plumbing this
  selection signal into the tier lookup is new work.
- (b) A `tier_map` entry per agent x tier combination, with `dispatch.agent_for()`
  extended to consult the *already-resolved* agent when looking up a tier (not just
  complexity/domain as today).
- (c) Defer entirely: implement tiers as agent-agnostic effort levels only ("T1 = xhigh
  reasoning on whichever agent"), and handle "prefer Claude for repo-level codegen" as
  a `routing.roles`-style override on specific task types once classification exists,
  layered on top of the tier's effort choice.

This design does not pick one of (a)/(b)/(c) — it's flagged as an open question because
the right answer depends on how task-purpose classification ends up working (see Open
Questions), and picking prematurely risks building plumbing the classifier can't
actually feed.

### D4: `build_cmd()` gains an `effort: Optional[str] = None` parameter, agent-specific translation stays local to `build_cmd()`

```python
if agent == "claude" and effort:
    cmd += ["--effort", effort]
if agent == "codex" and effort:
    cmd += ["-c", f"model_reasoning_effort={effort}"]
if agent == "opencode" and effort:
    cmd += ["--variant", effort]
```

Keeping the translation in `build_cmd()` (not upstream in `dispatch.py`/`live.py`) means
callers only ever pass a single semantic `effort` string; the agent-specific flag
shape is `build_cmd()`'s existing responsibility (it already does this for `--model`).

### D5: Review role ships now, independent of the rest of this change

`routing.roles: {review: {agent_cli: claude, agent_model: opus}}` requires zero new
schema (roles/agent-entry already supports `agent_cli`/`agent_model`). This can be
written into `~/.go/routing.yaml` today, with no code change at all — it's included in
this design for completeness (matches DEC-003's existing "independent reviewer" intent)
but has no implementation dependency on D1–D4.

## Risks / Trade-offs

- **[Risk] Effort value strings are unenforced, so a typo silently reaches the CLI as
  raw text** → Mitigation: `build_cmd()`'s translation is a straight passthrough
  (matches how `agent_model` already works — an invalid model string today would
  likewise just fail at the CLI, not at config-validation time). Consider a debug-log
  line echoing the resolved `(agent, model, effort)` triple per spawn so a bad value is
  visible in run logs rather than only in the underlying CLI's own error output.
- **[Risk] OpenCode's `--variant` may not support the assumed low/medium/high naming
  for `deepseek-v4-flash`** → Mitigation: do not build opencode tier entries with
  assumed values; the first implementation task in this area must be a live
  verification run (see Open Questions) before any opencode `effort` value ships in
  `~/.go/routing.yaml` or `model-defaults.yaml`.
- **[Risk] Task-purpose classification (a real, separate, harder problem) becomes an
  unbounded side-quest if tackled inside this change** → Mitigation: explicitly
  out-of-scope (see Non-Goals); this change ships the tier *definitions* and the
  *plumbing*, reachable via existing frontmatter (`complexity`) and explicit
  `routing.roles`, and stops there.
- **[Trade-off] Reusing `routing.tiers` for T1–T4 (D2) instead of a new dedicated
  schema section** keeps the change smaller and reuses tested code, at the cost of
  slightly overloading what "complexity" means (a T-tier name isn't really a complexity
  level in the same sense as `trivial`/`standard`/`hard`). Acceptable given the
  alternative (a parallel schema + parallel resolution path) is meaningfully more
  code for the same outcome.

## Migration Plan

1. Ship D1 (schema) + D4 (build_cmd plumbing) + D5 (review role) — additive, zero
   behavior change for any repo/task that configures nothing new. Full test coverage
   (unit tests for `_validate_agent_entry` with/without `effort`, `build_cmd()`
   translation per agent, backward-compat assertion that omitting `effort` is
   byte-identical to today).
2. Ship the plain 3-tier complexity fallback (trivial/standard/hard) using the
   already-existing `complexity` frontmatter convention — no classification work
   needed, immediately useful.
3. Verify opencode `deepseek-v4-flash` variant support live (see Open Questions) before
   writing any opencode `effort` value into an operator-facing config or doc.
4. Defer T1–T4 task→tier automatic routing until task-purpose classification (a
   separate proposal) exists. Until then, T1–T4 entries in `routing.tiers` are usable
   manually (an operator or a `/go` invocation can name a tier explicitly) but not
   automatically applied per task.

No rollback concerns beyond normal PR revert — every step above is additive and
gated behind configuration the operator must explicitly write.

## Open Questions

1. **Task-purpose classification** — how does a task get tagged with enough
   information to match entries like "security review" or "terminal-heavy agentic
   loop"? Candidates: (a) a new frontmatter field (`task_class`/`task_purpose`) set at
   whatever point today infers `complexity`/`domain`; (b) an explicit operator/route
   choice at dispatch time (no inference at all); (c) something else. Needs its own
   design pass — do not hand-wave as "an LLM classifies it live" (adds cost/latency/
   non-determinism to every dispatch).
2. **OpenCode `deepseek-v4-flash` variant support** — run
   `opencode run --model opencode/deepseek-v4-flash --variant <value> "..."` for each
   candidate value (start with the CLI's own documented examples: `high`, `max`,
   `minimal`) and confirm which are accepted and what the observable effect is (or
   whether the flag is silently ignored for this model). Must happen before any
   opencode `effort`/`variant` value is written into `~/.go/routing.yaml` or
   `~/.go/model-defaults.yaml`.
3. **Codex's full accepted `model_reasoning_effort` value set** — only `"low"` has been
   observed live (the operator's current config). Confirm the full set
   (`low`/`medium`/`high`/`max`?) against `codex exec --help`/official docs before
   relying on the operator's assumed four-value table.
4. **D3's agent-preference-within-tier mechanism** — which of (a)/(b)/(c) above, once
   task-purpose classification's shape is known (the right answer likely depends on it
   — e.g. if classification is an explicit per-task field, (a) or (b) become easy; if
   it's route-based, (c) may be simpler).
5. **Tier key naming convention** — literal `T1`/`T2`/`T3`/`T4` vs. a lowercase/hyphenated
   form, decided at implementation time for consistency with existing `trivial`/
   `standard`/`hard` complexity values already in use.
