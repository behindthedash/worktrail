## Why

Model selection today is either a single hardcoded per-agent default
(`spawnlib.default_model_for_agent()`, made operator-configurable via
`~/.go/model-defaults.yaml` in PR #114) or an already-existing but never-populated
`routing.tiers`/`routing.roles` mechanism in `policy.py`. Neither dimension controls
**reasoning effort** (confirmed live 2026-08-03: `build_cmd()` passes `--model` for all
three agents but has no effort/variant flag at all), and nothing classifies a task by
its actual **purpose** (architecture design vs. terminal-heavy automation vs. security
review vs. CRUD scaffolding) — today's `domain` frontmatter tag is a codebase-layer label
(frontend/backend/infra), not a task-purpose label.

The operator already runs a well-developed manual tiering policy — four tiers (T1 Deep
through T4 Trivia), each with a specific model+effort choice per agent, a task→tier
mapping table with real exceptions, and three governing rules — and wants the
orchestrator to apply it automatically instead of by hand. This proposal captures that
scheme as a full design so a later session can implement it without re-deriving the
policy or re-researching the underlying CLI mechanisms from scratch.

## What Changes

- Document the operator's four-tier (T1–T4) model+effort scheme as a formal capability:
  per-tier model+effort choice for each of the three agents (claude/codex/opencode), the
  task→tier mapping table (with its stated exceptions), the rare above-T1 escalation
  path, and the three governing rules (T2 is the default; dial before tier; never
  downgrade for schema/migration work).
- **BREAKING (schema, additive)**: extend the `routing.tiers`/`routing.roles` agent-entry
  schema (`policy._validate_agent_entry()`) with an optional `effort` field, alongside
  the existing `agent_cli`/`agent_model`. Absent today in every repo, so no existing
  config is invalidated — this is additive, not a breaking change to any current
  behavior.
- Add effort/variant plumbing from a resolved routing decision through
  `dispatch.agent_for()` → `LiveSpawn` → `spawn_agent()`/`build_cmd()`, translating one
  resolved `effort` value into the agent-specific flag: `--effort` (claude),
  `-c model_reasoning_effort=` (codex), `--variant` (opencode).
- Pin the `review` role to `claude:opus` by default (matches the codebase's own existing
  "independent reviewer" design intent for `JUDGMENT_ROLES`, DEC-003) — a settled
  sub-decision, not an open question.
- Ship a plain 3-tier complexity fallback (trivial/standard/hard →
  gpt-5.6-luna/terra/sol) as a smaller, independently-shippable slice if the full T1–T4
  scheme takes longer to land.
- Explicitly **out of scope for this change's implementation tasks** until resolved:
  automatic task-purpose classification (nothing today tags a task as "security review"
  vs. "CRUD scaffold"; this needs its own design decision on where/how that tagging
  happens) and confirming which effort/variant values `opencode`'s `deepseek-v4-flash`
  actually honors (its `--variant` flag exists, but the operator's assumed
  low/medium/high naming is unverified against this specific model).

## Capabilities

### New Capabilities
- `model-tier-routing`: per-agent model+effort tier resolution (T1–T4), task→tier
  mapping, the review-role pin, the 3-tier complexity fallback, and the effort/variant
  plumbing from a resolved routing decision to the spawned CLI's actual flags.

### Modified Capabilities
(none — no existing `openspec/specs/` capability covers agent/model routing today;
`routing.tiers`/`routing.roles` exist in `policy.py` but were never populated or
formally specified as a capability)

## Impact

- `src/worktrail/router/policy.py` — `_validate_agent_entry()` gains an optional
  `effort` field; `resolve_routing()`'s returned `fallback`/`roles`/`tiers` entries carry
  it through.
- `src/worktrail/orchestrator/dispatch.py` — `agent_for()` gains an effort dimension
  in its resolved `{"agent_cli", "agent_model"}` result (becomes `{"agent_cli",
  "agent_model", "effort"}`).
- `src/worktrail/orchestrator/spawnlib.py` — `build_cmd()` gains an `effort` parameter,
  translated into the correct per-agent flag; `default_model_for_agent()`-adjacent
  paths need an equivalent `default_effort_for_agent()` or similar for the fallback case.
- `src/worktrail/orchestrator/live.py` — `LiveSpawn`, `_effective_role_models()`, and
  the `~15` call sites threading `model` through today need the same treatment for
  `effort`.
- `tests/router/test_dashboard.py`, `tests/orchestrator/test_resilience_helpers.py`,
  `tests/orchestrator/test_spawnlib.py`, `tests/orchestrator/test_routing_e2e.py` — new
  coverage for effort resolution, translation per agent, and backward compatibility
  (no tier/effort configured behaves exactly as today).
- Not in this change's implementation scope: whatever eventually classifies a task's
  purpose (a new frontmatter field and its authoring-time inference step), and the
  live verification of opencode's `deepseek-v4-flash` variant support.
