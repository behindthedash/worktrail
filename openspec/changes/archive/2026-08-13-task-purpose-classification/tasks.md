## 1. Schema: purpose field on tasks

- [x] 1.1 Add optional `purpose: Optional[str]` to `taskformats/devkit/schema.py`'s
      `FIELD_SCHEMA`, alongside `complexity`/`domain`
- [x] 1.2 Add `purpose: str` to `taskformats/base.py`'s `TaskDict`
- [x] 1.3 Unit tests: a devkit task with `purpose` set loads and validates; a
      task with no `purpose` key is unaffected (matches `complexity`/
      `domain`'s existing coverage pattern)

## 2. compile.py: infer purpose from the repo's configured vocabulary

- [x] 2.1 In `conductor/compile.py`, resolve the target repo's
      `routing.purpose_tiers` (via `policy.py`, see task 4) before building
      `PROMPT`
- [x] 2.2 When `routing.purpose_tiers` is non-empty: extend `PROMPT` to
      request `"purpose"` per task, constrained to exactly the resolved
      table's keys (or omitted); when empty: do not request `purpose` at all
- [x] 2.3 Extend `TaskPlan` with a `purpose: str = ""` field; `_validate()`
      accepts an optional `"purpose"` key per row, dropping (with a warning)
      any value outside the injected vocabulary — same handling
      `_validate_agent_entry()` uses for malformed `agent_model`
- [x] 2.4 In `conductor/runplan.py`'s `apply_to_tasks()`, add `"purpose"` to
      the existing `for field in ("complexity", "review")` merge-only-if-
      absent loop
- [x] 2.5 Unit tests: repo with configured `routing.purpose_tiers` gets a
      `purpose`-requesting prompt and a validated, constrained result; repo
      with none gets no `purpose` request and every task's `purpose` stays
      unset; a returned value outside the injected vocabulary is dropped

## 3. routing.purpose_tiers: policy schema + resolution

- [x] 3.1 Add `routing.purpose_tiers: Dict[str, str]` validation to
      `policy.py` (plain string-to-string map, no agent-entry shape)
- [x] 3.2 Thread `purpose_tiers` through `resolve_routing()`'s returned dict,
      alongside `fallback`/`roles`/`tiers`
- [x] 3.3 Unit tests: a configured `routing.purpose_tiers` table resolves and
      validates; an unconfigured/empty table resolves to `{}`; a malformed
      entry (non-string value) is dropped with a warning

## 4. dispatch.agent_for(): purpose-first tier resolution + agent-aware lookup

- [x] 4.1 Add a `purpose_tier_map: Optional[Dict[str, str]] = None` parameter
      to `agent_for()`
- [x] 4.2 Implement tier-name resolution: `purpose_tier_map.get(task.get
      ("purpose"))` if it resolves, else `task.get("complexity")` — for
      `implement`/`fix`/`cleanup` roles only; `JUDGMENT_ROLES` untouched
- [x] 4.3 Implement the two-step `tier_map` lookup: try
      `tier_map.get((f"{tier}-{agent}", domain))` first (`agent` =
      `default_agent or "claude"`), then `tier_map.get((tier, domain))`,
      preserving today's exact fallback-to-run-default behavior when neither
      matches
- [x] 4.4 Unit tests: purpose-derived tier takes precedence over complexity;
      falls back to complexity when purpose doesn't resolve;
      `JUDGMENT_ROLES` never consult `purpose`/`purpose_tier_map`; agent-aware
      key (`t1-deep-codex`) preferred over plain key (`t1-deep`) when both
      exist; falls back to plain key when no agent-specific entry exists;
      byte-identical output to pre-change `agent_for()` when no `purpose`/
      `purpose_tier_map`/agent-aware keys are involved

## 5. Wire purpose_tier_map into live dispatch call sites

- [x] 5.1 Thread `resolve_routing()`'s `purpose_tiers` result through to every
      `agent_for()` call site in `orchestrator/live.py`
      (`_effective_role_models()` and any other caller that already threads
      `tier_map`/`role_agent_map` through)
- [x] 5.2 [e2e] Regression test: full suite + golden check (`orchestrate
      check`) green with `routing.purpose_tiers` populated in a test policy,
      confirming no interaction with existing `dispatch.agent_for()`
      precedence rules for `JUDGMENT_ROLES` — mirrors `model-tier-routing`
      task 4.3's coverage shape

## 6. Documentation

- [x] 6.1 Document `routing.purpose_tiers` in the same README/skill doc
      location `model-tier-routing` used for `routing.roles`/`routing.tiers`
      (task 4.1 of that change), including the worked example: mapping
      `architecture-design`/`agentic-automation`/`security-review`/
      `scaffolding`/`bulk-mechanical`/`trivial` to T1–T4 tier names
- [x] 6.2 Update `openspec/changes/model-tier-routing/tasks.md`'s §5 (5.1,
      5.2) to reference this change as their resolution — bookkeeping only,
      no code change to that already-merged change
