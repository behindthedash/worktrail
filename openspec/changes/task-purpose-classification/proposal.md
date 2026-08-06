## Why

`model-tier-routing` (PR #120, merged 2026-08-04) shipped the T1–T4 tier
definitions, the effort/variant plumbing, and the review-role pin, but
explicitly deferred the one thing that would make the scheme apply itself:
nothing today classifies a task's actual **purpose** (architecture design vs.
terminal-heavy automation vs. security review vs. CRUD scaffolding). Tasks
5.1/5.2 in that change's `tasks.md` name this gap directly and defer it to
"a follow-up proposal" (design.md Non-Goals, Migration Plan step 4, Open
Question 1) — this is that proposal.

Without it, the operator's T1–T4 tiers are reachable only by naming a tier
explicitly (an operator or a `/go` invocation typing `t2-build-claude` by
hand). The manual task→tier mapping table the operator already runs in their
head — the actual value of the T1–T4 scheme — cannot be applied
automatically. `~/.go/routing.yaml`'s own committed comments confirm this is
live, current pain, not a hypothetical: its `t1-deep-claude`/`t1-deep-codex`/
`t1-deep-opencode` per-agent tier keys are annotated "NOT auto-applied per
task: nothing classifies a task's purpose yet (see handoff
20260803-173728-write-the-follow-up-openspec)" and "[keying by agent]
sidesteps D3 entirely with zero new code, since manual selection was already
the only access path for T1-T4" — i.e. the operator worked around both gaps
(no purpose signal, no agent-aware tier lookup) by hand, and is waiting on
this design to stop doing that by hand.

## What Changes

- Add an optional `purpose` field to the task schema (`taskformats/devkit/
  schema.py`'s `FIELD_SCHEMA`, `taskformats/base.py`'s `TaskDict`), parallel
  to the existing optional `complexity`/`domain` fields. Absent today in
  every task; an entry with no `purpose` behaves exactly as it does now
  (additive, not breaking).
- Populate `purpose` the same way `complexity`/`review` are already
  populated today: extend `conductor/compile.py`'s existing authoring-time
  inference step (the LLM call `spec-to-tasks`/`compile` already makes per
  task, which infers `complexity: low|medium|high` and `review:
  light|standard|deep`) to also infer `purpose` from a small, fixed
  enumeration. This reuses tested infrastructure — the JSON schema, the
  `TaskPlan` dataclass, `runplan.apply_to_tasks()`'s merge-only-if-absent
  rule — instead of inventing a second inference mechanism. Concretely
  answers design.md Open Question 1's "(b) an inference step at authoring
  time" in favor of extending the one that already exists, over "(a) a new
  frontmatter field set by something else" (nothing else sets task
  frontmatter today) or live per-dispatch LLM classification (design.md
  Open Question 1 explicitly warns against this: cost/latency/
  non-determinism on every dispatch).
- Add a `routing.purpose_tiers` policy table: `{<purpose>: <tier-name>}`,
  resolved alongside `routing.tiers`/`routing.roles` in `policy.py`. This is
  the operator's task→tier mapping table (today implicit, in their head),
  now expressible in `go-policy.yaml`/`routing.yaml`. A task with a `purpose`
  that has no `routing.purpose_tiers` entry, or a repo with no
  `routing.purpose_tiers` table at all, resolves exactly as it does today
  (falls through to `complexity`-keyed `routing.tiers`, then the run
  default) — additive.
- Resolve design.md D3 (agent-preference-within-tier): formalize the
  `<tier>-<agent>` key convention already adopted informally in
  `~/.go/routing.yaml` (`t1-deep-claude`, `t1-deep-codex`, `t1-deep-opencode`,
  ...) as a real `dispatch.agent_for()` capability instead of a naming
  convention nobody's code actually reads. `agent_for()` gains a second
  tier-lookup pass: once a task's tier is known (from `purpose` via
  `routing.purpose_tiers`, or from `complexity` as today) and — only for
  `implement`/`fix`/`cleanup` roles, after `role_agent_map` and before the
  plain tier match — a candidate agent has already been selected by the run's
  own fallback/capacity resolution, look up `<tier>-<agent>` in
  `routing.tiers` first; fall back to the plain `<tier>` key if no
  per-agent entry exists. This is design.md D3 option (b) ("a `tier_map`
  entry per agent x tier combination, with `dispatch.agent_for()` extended
  to consult the already-resolved agent"), made concrete now that the key
  shape is already proven out in production config and only the consulting
  code was ever missing.
- **BREAKING (schema, additive only)**: none of the above changes behavior
  for any repo/task that configures no `purpose` frontmatter and no
  `routing.purpose_tiers` table — same additive guarantee `model-tier-routing`
  made for `effort`.

## Capabilities

### New Capabilities
- `task-purpose-classification`: the `purpose` task-schema field, its
  authoring-time inference (extending `compile.py`'s existing prompt), the
  `routing.purpose_tiers` mapping table, and the agent-aware
  `<tier>-<agent>` tier-lookup pass in `dispatch.agent_for()` that resolves
  design.md D3.

### Modified Capabilities
(none — `model-tier-routing`'s own capability spec was never synced from
`openspec/changes/model-tier-routing/specs/` into `openspec/specs/`, so
there is no committed `model-tier-routing` capability yet to modify; this
change's own spec stands alone and references it informally)

## Impact

- `src/worktrail/taskformats/devkit/schema.py` — `FIELD_SCHEMA` gains an
  optional `purpose` field, same shape as `complexity`/`domain`.
- `src/worktrail/taskformats/base.py` — `TaskDict` gains `purpose: str`.
- `src/worktrail/conductor/compile.py` — `PROMPT` gains a `purpose` output
  field with an enumerated value set; `_validate()`/`TaskPlan` carry it
  through alongside `complexity`/`review`.
- `src/worktrail/conductor/runplan.py` — `apply_to_tasks()`'s
  merge-only-if-absent loop (`for field in ("complexity", "review")`) gains
  `"purpose"`.
- `src/worktrail/router/policy.py` — new `routing.purpose_tiers` table,
  validated and resolved alongside the existing `routing.tiers`/
  `routing.roles`/`routing.fallback` in `resolve_routing()`.
- `src/worktrail/orchestrator/dispatch.py` — `agent_for()` gains the
  `<tier>-<agent>` lookup pass described above, for `implement`/`fix`/
  `cleanup` roles only (unchanged for `JUDGMENT_ROLES` — DEC-003's
  independent-reviewer precedence is untouched).
- `~/.go/routing.yaml` (operator config, not code) — once this ships, the
  existing `t1-deep-*`/`t2-build-*`/`t3-bulk-*`/`t4-trivia-*` entries need no
  edits (the key shape already matches); add `routing.purpose_tiers` to
  make them reachable automatically.
- `tests/conductor/test_compile.py`, `tests/router/test_policy.py`,
  `tests/orchestrator/test_dispatch.py` — new coverage for `purpose`
  inference, `routing.purpose_tiers` resolution, and the agent-aware tier
  lookup, plus a backward-compat assertion that omitting `purpose`/
  `routing.purpose_tiers` reproduces today's exact `agent_for()` output.
- `openspec/changes/model-tier-routing/tasks.md` — tasks 5.1/5.2 point at
  this change once it lands; not modifying `model-tier-routing`'s own merged
  code, only its bookkeeping.
