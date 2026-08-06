## Context

`model-tier-routing` (PR #120, merged 2026-08-04) added an `effort` field to
`routing.tiers`/`routing.roles` agent-entries and wired it through to
`build_cmd()`, and captured the operator's manual T1–T4 (Deep/Build/Bulk/
Trivia) tier scheme as `routing.tiers` entries in `~/.go/routing.yaml`. Two
things were explicitly deferred (`tasks.md` §5, design.md Non-Goals/Open
Question 1):

1. Nothing classifies a task's **purpose** — architecture design vs.
   terminal-heavy automation vs. security review vs. CRUD scaffolding, etc.
   Today's `complexity`/`domain` frontmatter (`taskformats/base.py`'s
   `TaskDict`) label difficulty and codebase layer, not purpose. Without a
   purpose signal, the T1–T4 tiers are reachable only by an operator naming
   one explicitly.
2. Design.md's D3 ("the prefer-Claude-vs-prefer-Codex nuance is NOT solved by
   a tier entry alone") was left open, pending exactly this classification
   work — deciding between three sketched options (a/b/c), none picked.

Since PR #120 merged, the operator has already worked around both gaps by
hand in `~/.go/routing.yaml`: T1–T4 entries are keyed `t1-deep-claude`,
`t1-deep-codex`, `t1-deep-opencode`, etc. — one key per (tier, agent) pair —
with a comment explaining this "sidesteps D3 entirely with zero new code,
since manual selection was already the only access path for T1-T4." That is
real evidence for this design, not a hypothetical: the key *shape* D3 needs
already exists in production config; only the code that would look it up
automatically was ever missing, because nothing yet decides which tier a
task belongs to without a human typing it.

`conductor/compile.py` already runs one authoring-time LLM inference pass per
change — the `spec-to-tasks`/`compile` step that infers each task's `files`,
`deps`, `complexity`, and `review` from the change's `proposal.md`/
`design.md`/`specs/**`. This is the "inference step at authoring time"
design.md Open Question 1 asked about, already built and already tested
(`runplan.apply_to_tasks()`'s merge-only-if-absent rule, `TaskPlan`
dataclass, `_validate()`'s all-or-nothing rejection). It infers `complexity`
and `review` as free-standing enumerated strings today; extending it to also
infer `purpose` is additive to an existing, proven mechanism rather than a
new one.

## Goals / Non-Goals

**Goals:**
- Give tasks an optional `purpose` signal, populated automatically at spec
  authoring time (the same point `complexity`/`review` are populated today),
  with zero effect on any task/repo that doesn't opt in.
- Let an operator map `purpose` values to T-tier names via a new
  `routing.purpose_tiers` policy table, reusing (not duplicating)
  `routing.tiers`' existing agent-entry validation and resolution.
- Resolve D3: make `dispatch.agent_for()` actually consult the `<tier>-
  <agent>` key shape already live in `~/.go/routing.yaml`, so those entries
  stop being decorative.
- Preserve exact current behavior for every task/repo that sets no `purpose`
  frontmatter and configures no `routing.purpose_tiers` table (hard
  requirement, same guarantee `model-tier-routing` made for `effort`).

**Non-Goals (this change does not implement):**
- A fixed, hardcoded taxonomy of purpose values. The taxonomy is operator
  config (`routing.purpose_tiers`' own keys), not a constant in code — see
  D2. This avoids re-litigating "what counts as architecture design" in
  code review every time an operator's mental model shifts.
- Live, per-dispatch purpose classification. Ruled out for the same reason
  `model-tier-routing`'s Open Question 1 ruled it out: cost, latency, and
  non-determinism on every single dispatch, for a signal that changes only
  when the task itself changes.
- Any change to `spawnlib.default_model_for_agent()`'s existing precedence,
  or to how the run's own agent (`default_agent`/the fallback chain) gets
  selected in the first place. This change only adds a lookup that consults
  whichever agent was already chosen — it does not change how that choice is
  made.
- Retroactively classifying tasks from specs authored before this change
  ships. `purpose` is populated going forward, at the next `compile.py` run
  for a given change; there is no backfill mechanism here.

## Decisions

### D1: `purpose` surfaces via `compile.py`'s existing inference pass, not a new mechanism

Extend the same JSON payload `compile.py`'s `PROMPT` already asks the
authoring-time agent to emit — `{"id", "files", "deps", "complexity",
"review"}` — with one more optional key, `"purpose"`. `_validate()` accepts
it the same way it accepts `complexity`/`review` today (a plain string,
unenforced against a fixed enum in *code* — see D2 for why the enum itself
lives in config instead). `runplan.apply_to_tasks()`'s existing
merge-only-if-absent loop (`for field in ("complexity", "review"): if not
m.get(field) and getattr(tp, field): m[field] = ...`) gains `"purpose"`
alongside them.

**Alternative considered**: a second, dedicated inference pass just for
purpose. Rejected — `compile.py` already reads `proposal.md`/`design.md`/
`specs/**` once per change to answer "what files does each task touch,
how complex is it"; asking it one more question in the same pass costs
nothing extra (one model call either way) and keeps `TaskPlan` as the single
place a task's inferred metadata lives.

**Alternative considered**: a new frontmatter field an operator or the
brainstorm/spec-to-tasks author sets by hand. Rejected — this is exactly
the status quo `model-tier-routing` shipped (manual `routing.tiers` naming),
which is the thing task 5.1 exists to move past. Nothing today authors task
frontmatter *except* `compile.py`'s inference pass and the devkit format's
own scaffolding; there's no natural second place for a human to set it that
wouldn't just become "manually pick a tier," restated.

### D2: `purpose`'s value set is operator config (`routing.purpose_tiers`' keys), not a code-level enum

`compile.py`'s `PROMPT` is built per-repo already (it reads that repo's
`proposal.md`/`design.md`). Extend it to load the target repo's
`routing.purpose_tiers` table (via `policy.py`, already resolved per-repo)
and inject its keys verbatim as the closed value set: *"purpose: one of
[architecture-design, agentic-automation, security-review, scaffolding,
bulk-mechanical, trivial], or omit if none clearly fits."* (Those six are
this design's suggested starter set, matching the operator's own T1–T4
examples — Deep ≈ architecture-design/security-review, Build ≈
agentic-automation/scaffolding, Bulk ≈ bulk-mechanical, Trivia ≈ trivial —
but they are written into the *operator's* `routing.purpose_tiers` config,
not hardcoded in `compile.py`, so the operator can rename, add, or remove
categories without a code change.)

If a repo has no `routing.purpose_tiers` table configured, `compile.py`
does not ask for `purpose` at all — there is no target vocabulary to
classify into, so the field is simply absent, identical to today's output
for a repo with no tier configuration.

**Alternative considered**: a fixed enum validated in `_validate_agent_entry`-
adjacent code, matching how `complexity`/`review` are (informally) treated
as fixed value sets. Rejected — `complexity`/`review`'s value sets
(`low|medium|high`, `light|standard|deep`) are effectively universal
software-engineering vocabulary; "purpose" is inherently operator- and
repo-specific (a docs-only repo's task purposes look nothing like a
security-critical service's), so hardcoding it in `compile.py` would need
per-repo overrides anyway — config *is* the override mechanism, so skip the
hardcoded default and go straight to config.

### D3 (resolves model-tier-routing design.md's D3): agent-aware tier lookup via the already-proven `<tier>-<agent>` key shape

`dispatch.agent_for()`'s `tier_map` lookup (currently `tier_map.get
((task.get("complexity"), task.get("domain")))`) gains a **first** lookup
attempt before the existing one, only for `implement`/`fix`/`cleanup` roles
(`JUDGMENT_ROLES` are untouched — DEC-003's independent-reviewer precedence
already excludes tier_map entirely for those roles):

```python
tier = _resolve_tier(task, purpose_tier_map)   # see below
domain = task.get("domain")
if tier_map and tier:
    agent = default_agent or "claude"
    match = tier_map.get((f"{tier}-{agent}", domain))   # NEW: agent-aware
    if match is None:
        match = tier_map.get((tier, domain))            # existing behavior
    ...
```

`_resolve_tier(task, purpose_tier_map)` returns, in order: (1)
`purpose_tier_map.get(task.get("purpose"))` if the task has a `purpose` and
`routing.purpose_tiers` resolves it to a tier name; (2) `task.get
("complexity")` as today. This makes `purpose` a *higher-precedence*, more
specific signal than `complexity` when both are present, without removing
the `complexity` path repos already use.

No change to `_validate_routing_tiers`' key format (`<complexity>[/
<domain>]`, design.md D2 of `model-tier-routing`, unchanged): a `<tier>-
<agent>` string is still just a string in the "complexity" slot, exactly as
the operator's own `~/.go/routing.yaml` already writes it. Only
`agent_for()`'s lookup order changes — the key parsing, `policy.py`
validation, and `resolve_tier_map()` all stay as they are.

This picks model-tier-routing design.md's option **(b)** ("a `tier_map`
entry per agent x tier combination, with `dispatch.agent_for()` extended to
consult the already-resolved agent") — made concrete now that `purpose`
supplies the missing "which tier" signal and the `<tier>-<agent>` key shape
is already validated in production use, not merely proposed.

**Alternative considered**: option (a), two tiers per T-level selected by
"whatever already decided which agent is live." Rejected as a distinct
mechanism — it's the same key shape as (b), just resolved by a different
(and vaguer) piece of logic ("whatever decided"); (b) names the exact
existing parameter (`default_agent`) that already carries this information
into `agent_for()`, so there's no new plumbing to invent.

**Alternative considered**: option (c), agent-agnostic tier effort only,
with agent preference as a `routing.roles`-style override per purpose.
Rejected in favor of (b) because `routing.roles` is keyed by *role name*
(`implement`/`review`/`fix`/`cleanup`), not by task attribute — extending it
to also match on `purpose` would need the same kind of new lookup dimension
(b) already adds to `tier_map`, but on a mechanism (`role_agent_map`) whose
existing contract (DEC-003, `JUDGMENT_ROLES`) is about role identity, not
task classification. Reusing `tier_map` (whose contract is already
"resolved by task attributes") keeps the two mechanisms' responsibilities
separate: `role_agent_map` = who reviews; `tier_map` = which tier/agent
combination a task's own nature calls for.

### D4: `routing.purpose_tiers` is validated and resolved in `policy.py`, alongside `routing.tiers`

`{<purpose>: <tier-name>}`, a plain string-to-string map — no agent-entry
validation needed (a tier *name*, not an agent entry, is the value).
`resolve_routing()`'s returned dict gains a `purpose_tiers` key alongside
`fallback`/`roles`/`tiers`. An unconfigured or empty table behaves
identically to no configuration (D1/D3's `_resolve_tier()` falls through
to `complexity` immediately).

## Risks / Trade-offs

- **[Risk] `purpose` values drift from `routing.purpose_tiers`' configured
  keys if the prompt and the config table are read at different times (e.g.
  policy edited between spec authoring and orchestration)** → Mitigation:
  `compile.py` reads `routing.purpose_tiers` fresh at spec-authoring time
  and injects its *exact current keys* into the prompt (D2), so a task's
  `purpose` is only ever chosen from that moment's valid set. A later config
  edit that removes a key simply makes `_resolve_tier()` fall through to
  `complexity` for tasks classified under the now-missing purpose — no
  crash, matches D1's `agent_model`/`effort` precedent of "an invalid value
  degrades to fallback, never errors."
- **[Risk] Task purpose misclassification routes a task to the wrong tier
  (e.g. a security-sensitive task inferred as `scaffolding`)** → Mitigation:
  same acceptance already established for `complexity`/`review` — a wrong
  value resolves to a *valid but suboptimal* tier/agent choice, not a
  safety bypass (the tier scheme controls model/effort selection, not
  authorization or review requirements; `JUDGMENT_ROLES`' independent-
  reviewer guarantee is untouched regardless of `purpose`). An operator who
  finds systematic misclassification can tighten `routing.purpose_tiers`'
  key set or the task's own explicit `agent`/`complexity` override (both
  higher-precedence than `tier_map` already).
- **[Trade-off] Config-driven taxonomy (D2) means two repos can use the
  same purpose word to mean different tiers**, unlike a universal code-level
  enum. Acceptable — the taxonomy is inherently operator/repo-specific (see
  D2's rationale), and `routing.purpose_tiers` already lives in the same
  per-repo `go-policy.yaml`/machine-wide `routing.yaml` as `routing.tiers`
  itself, so there is exactly one place this mapping needs to stay
  consistent, not several.
- **[Trade-off] `agent_for()` gains a second dict lookup per implement/fix/
  cleanup dispatch** (`f"{tier}-{agent}"` before `tier`). Negligible cost
  (a dict `.get()` on an in-memory, already-resolved policy structure), and
  only reached when `tier_map` is non-empty (repos with no tiers configured
  never touch the new code path at all).

## Migration Plan

1. Ship D1 (schema: `purpose` on `TaskDict`/devkit `FIELD_SCHEMA`) + D1's
   `compile.py`/`runplan.py` plumbing — additive, zero behavior change for
   any repo/task configuring nothing new. Unit tests: `_validate()` with/
   without `purpose`; `runplan.apply_to_tasks()` merge with/without
   `purpose`; a repo with no `routing.purpose_tiers` never asks `compile.py`
   for `purpose`.
2. Ship D4 (`routing.purpose_tiers` schema + `resolve_routing()` plumbing)
   — additive; an unconfigured table changes nothing.
3. Ship D3 (`agent_for()`'s agent-aware tier lookup) — additive in code
   (behavior only changes for a `tier_map` that actually contains
   `<tier>-<agent>` keys), but **immediately live in the operator's own
   `~/.go/routing.yaml`** the moment they add a `routing.purpose_tiers`
   table, because the `t1-deep-*`/`t2-build-*`/`t3-bulk-*`/`t4-trivia-*`
   entries already exist there today. No entry edits needed on the
   operator's side beyond adding the new table — this is the whole point of
   the `<tier>-<agent>` convention already anticipating this design (see
   Context).
4. Operator populates `routing.purpose_tiers` in `~/.go/routing.yaml`
   mapping their real task→tier table (the one referenced but never
   formalized in `model-tier-routing`'s proposal.md) into the new config
   shape. Operator action, not code — same "written into `~/.go/
   routing.yaml` today, with no code change at all" pattern `model-tier-
   routing`'s D5 (review role) already used.

No rollback concerns beyond a normal PR revert — every step is additive and
gated behind configuration the operator must explicitly write, matching
`model-tier-routing`'s own migration plan.

## Open Questions

1. **Starter purpose taxonomy** — this design suggests six starter values
   (architecture-design, agentic-automation, security-review, scaffolding,
   bulk-mechanical, trivial) as a `routing.purpose_tiers` example, mirrored
   from the operator's own T1–T4 language. The operator may want a
   different split once they see real `compile.py` output against it;
   nothing in the implementation depends on this exact list (D2).
2. **`compile.py` prompt cost** — asking for one more field in an already-
   existing prompt/response should be near-zero marginal cost, but this
   should be confirmed against real token counts during implementation
   rather than assumed.
