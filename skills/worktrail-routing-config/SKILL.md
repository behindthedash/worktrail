---
name: worktrail-routing-config
description: >
  Configure Worktrail's agent routing table (`~/.worktrail/routing.yaml`, or a repo-local
  `routing:` policy block) — which harness/model executes each role and tier, and how
  fallback between them works. Use when the operator wants to add or remove a harness/adapter
  (claude/codex/opencode), add a model to an existing harness (e.g. adding a DeepSeek model to
  opencode), pin or change who does code review, add a new tier or purpose mapping, change the
  default tier, or is confused about which harness actually served a spawn (e.g. "why did this
  use Codex when I only configured Claude"). Trigger phrases: "routing.yaml", "add a harness",
  "disable a harness", "add an adapter", "remove an adapter", "add a model", "add deepseek to
  opencode", "change the reviewer", "pin the reviewer", "routing tiers", "routing targets",
  "default_tier", "independent reviewer", "why did codex get called". This skill is about
  *editing the config file*; it does not cover `/go`'s own dispatch mechanics (see
  `worktrail-go`'s SKILL.md Phase 7 for how `tier_for()`/`select_cell()` resolve it at runtime).
allowed-tools: Read, Edit, Write, Bash
---

# Routing Configuration

## Overview

Worktrail spawns headless agent workers (implement/fix/cleanup/review/...) through one
resolved routing table: `targets` (which harness/pool each named target uses) crossed with
`tiers` (which model/effort each target runs at, per tier row), plus `roles`/`purposes` that
pick a tier for a given task. This skill is the operator's cookbook for editing that table —
adding/removing harnesses and models, pinning the reviewer, adding tiers — and the gotchas that
bite when two settings interact non-obviously.

**File location:** `~/.worktrail/routing.yaml` (machine-wide, `WORKTRAIL_ROUTING_FILE` env var
overrides the path) is read whenever a repo's own policy has no `routing:` block. A repo-local
`routing:` block in that repo's `.worktrail/policy.yaml` **replaces the machine-wide file
entirely** for that repo — it is not merged key-by-key. A non-empty repo-local block means the
machine-wide file is never read for that repo at all.

**Where the actual resolution logic lives** (read only if you need to verify behavior, not to
edit routing): `src/worktrail/router/policy.py` (`resolve_routing`, validation/legacy-key
rejection), `src/worktrail/runtime/selection.py` (`select_cell` — the fallback walk),
`src/worktrail/orchestrator/dispatch.py` (`tier_for` — role → tier resolution). The formal
behavioral contract is `openspec/specs/model-tier-routing/spec.md` and
`openspec/specs/drain-operator-config/spec.md`.

## Concepts, briefly

| Key | What it is |
|---|---|
| `targets` | Named launch targets, each `{harness: claude\|codex\|opencode, pool: subscription\|free\|api}`. **File order is the fallback order** for any tier row that doesn't reorder it via `prefer`. |
| `tiers` | `{tier_name: {target_name: {model, effort?}}}`. A target with no cell in a row cannot serve that tier — the walk skips straight past it. |
| `roles` | Per-role override (`review`/`resolve`/`ci-fix`/`assembly-resolve`/`implement`/`fix`/`cleanup`): `{tier, prefer?, independent?}`. |
| `purposes` | `{purpose_value: tier_name}` — routes implement/fix/cleanup tasks by their `purpose` frontmatter instead of `complexity`. |
| `default_tier` | The tier used when nothing more specific matches. |
| `drain` | Drain-loop-only settings (currently just `max_workers`). No `agent`/`fallback_agents` keys — those are retired, rejected loudly with a migrate hint. |

Full mechanics (precedence order, `prefer` vs `independent`, purpose vs complexity) are in
`worktrail-go`'s SKILL.md Phase 7 — this skill won't restate them, only the parts you need to
make a specific edit safely.

## How-tos

See `references/how-to.md` for the specific recipes:
- Add a new harness/adapter (target)
- Disable a harness/adapter without deleting its config
- Add a new model to an existing harness (including adding e.g. a DeepSeek model to `opencode`)
- Add or remove a tier
- Route a purpose to a tier
- Pin, change, or unpin the code reviewer
- Change `default_tier`
- Validate a change before trusting it

## Gotchas

See `references/gotchas.md` for the non-obvious interactions this table produces, especially:
- `independent: true` silently overrides `prefer` when the preferred target shares a harness
  with whichever harness implemented the task — this is the #1 cause of "I only configured
  Claude, why did Codex just get spawned."
- `prefer` only reorders a tier row; the *entire* row (in target file order) is the fallback
  chain — there is no separate fallback list to also check.
- Legacy top-level keys (`routing.agents`, `routing.fallback`, `routing.purpose_tiers`,
  `drain.agent`, `drain.fallback_agents`) are rejected outright, not silently ignored — a
  ruleset written against an older Worktrail version needs `worktrail-routing --migrate`.
- `api`-pool targets (`openrouter`/`api`) are dropped from selection unless the target sets
  `api_opt_in: true` — this is deliberate, not a bug, to keep subscription-first spend honest.
- Effort vocabulary differs per harness: `claude`/`codex` take `low`/`medium`/`high` (`codex`
  also accepts `minimal`); `opencode` has none — its `effort` field is ignored and model
  selection is the only lever.
- A cell can be stuck `unavailable` in `~/.worktrail/agent-capacity.json` after a stale billing
  gate outlives its `retry_after` — check that file before assuming a routing-table edit didn't
  take effect.

## Verify a change took effect

Any **already-running** drain loop or long-lived orchestrator process loaded `routing.yaml`
into memory at startup — it will not see an edit until restarted. A one-off `/go` invocation
picks up the current file on its next run with no restart needed.

```bash
worktrail-routing --check                 # validates syntax + rejects legacy keys
worktrail-policy --repo <repo> --resolve-routing "x:x" --json   # prints the fully resolved table
```
