## Context

Verified state at worktrail `d080550` (2026-08-27):

- `router/policy.py` resolves `~/.worktrail/routing.yaml` into `{agents, tiers, fallback,
  roles, purpose_tiers, drain}`; `resolve_tier_map()` flattens tiers to
  `(f"{tier}-{agent}", domain)` keys.
- `dispatch.agent_for()` reads one column of the tier matrix (the run-default agent's) and
  returns `{agent_cli, agent_model, effort}`; judgment roles consult only `roles`/run default.
- `spawnlib.spawn_agent()` builds `[(agent, model)] + [(hop, default_model_for_agent(hop))]`
  and walks `agent_capacity.check` per hop; the in-spawn hop fires only on a parsed
  session-limit notice (`spawnlib.py:941`); other infra failures retry the same target and
  return an empty `SpawnResult`.
- `live.py:2414-2416` sets `effective_fallback=None` when a judgment role resolves to an
  agent other than the run default.
- `drain.py` builds candidates from `drain.agent + drain.fallback_agents`, gates on
  `agent-capacity.json` keyed `provider` or `provider:model`, and spawns the front-door
  session with no `--model`. `CAPACITY_FAILURE_CLASSES = {auth, billing}`.
- `runtime/selection.py` already has `select_execution_target(catalog, capacity=...)` with
  `NoExecutionTarget`; `runtime/routing_source.routing_candidates()` derives its catalog from
  `agents`+`tiers`.
- Harness facts verified live: `claude --bare` → auth strictly `ANTHROPIC_API_KEY`/
  `apiKeyHelper`; `codex login status` → "Logged in using ChatGPT", `codex login
  --with-api-key` exists; `opencode models` lists `opencode/*` (Zen), `openrouter/*`,
  `google/*` ids and `opencode auth list` shows Zen + OpenRouter credentials; a retired
  `opencode/*` id yields exit 1 + `UnknownError "Unexpected server error"`, indistinguishable
  from a transient outage by text alone.

## Goals / Non-Goals

Goals: one selector for every spawn path; preference expressed as data (target order +
pool) so "subscription first, free second, API only by opt-in" is a property of the file;
fallback that stays in-tier; review that degrades instead of dying; a retired model that
gates its own cell loudly; a config an operator edits one cell at a time when models churn.

Non-Goals: live pricing lookups; automatic model discovery for claude/codex (no listing
command found — claude aliases `opus/sonnet/haiku/fable` are stable, codex relies on the
failure class); dual-reading legacy keys; changing task-purpose inference in `compile.py`
beyond the vocabulary table's new entries.

## Decisions

**D1 — Target = harness + pool (+ auth).** `targets` is an ordered mapping; order is
preference. `harness ∈ {claude, codex, opencode}` (the CLI `build_cmd` spawns);
`pool ∈ {subscription, free, api}`; `api_opt_in: true` is required on any `api`-pool target
or it is skipped with a warning (the existing `api_opt_in` semantic, now applying to every
API lane including opencode Zen). Two targets may share a harness. Names are free-form and
are the keys tier cells use.

```yaml
targets:
  claude-sub:    {harness: claude,   pool: subscription}
  codex-sub:     {harness: codex,    pool: subscription}
  opencode-free: {harness: opencode, pool: free}
  claude-api:    {harness: claude,   pool: api, api_opt_in: true, auth: {env: ANTHROPIC_API_KEY}}
  openrouter:    {harness: opencode, pool: api, api_opt_in: true}
```

Alternative rejected: keep `agents` keyed by harness and add a `pool` per tier cell — a
pool is an account property, not a model property, and it is what a capacity gate must key on.

**D2 — Tier rows keyed by target; a missing cell means "cannot serve".** `tiers.<row>.<target>
= {model, effort?}`. Rows are named freely (`t1-deep`…`t4-trivia` remain the shipped names);
`purposes` (renamed from `purpose_tiers`, same shape) and `roles` point at rows. `default_tier`
(top-level string) replaces `agents.<x>.default_model`; anything that previously asked for a
harness default model asks `select_cell(default_tier)` instead.

**D3 — One selector.** `select_cell(routing, tier, *, prefer=None, exclude_harness=None,
capacity, now)` → `Cell(target, harness, model, effort, pool, auth)`:
1. order = targets in file order; if `prefer` names a target with a cell in this row, it
   moves to the front;
2. drop `api`-pool targets lacking `api_opt_in`; drop targets with no cell in the row;
3. if `exclude_harness` is set, partition: targets on other harnesses first, then the excluded
   ones (soft exclusion — independence is a preference, never a reason to fail);
4. first cell whose `(target, model)` is not gated in `agent-capacity.json` wins;
5. none → `NoExecutionTarget` listing every cell and its gate.
Pure, clock/capacity injected, deterministic. Callers: `LiveSpawn.__call__`, `spawn_agent`
(re-selects on hop with the primary cell excluded), drain `select_available_agent` (row =
`default_tier`), `compile._default_spawn`, `skill_dispatch.main`, `routing_cli --show`.

**D4 — Roles resolve to a tier, never a literal CLI/model.** `roles.<role> = {tier, prefer?,
independent?}`. `dispatch.agent_for()` becomes `tier_for(role, task, roles, purposes,
default_tier)` returning `(tier, prefer, independent)`; the selector does the rest. `review`
defaults to `{tier: t1-deep, independent: true}`; `independent` passes the implementer's
harness as `exclude_harness`. The `live.py` judgment-pinned/no-fallback branch is deleted:
independence is now expressed in the selector, so a fallback can no longer "erode" it — the
same-harness reviewer is the last resort, and the run record names which cell served. The
operator's example ("sonnet for t2-build, gpt-5.6-sol for review") is
`roles.review: {tier: t1-deep, prefer: codex-sub}` — or a dedicated row
`tiers.review: {codex-sub: {model: gpt-5.6-sol}, claude-sub: {model: opus}}` with
`roles.review: {tier: review}`; both fall back along the row.

**D5 — Capacity keys on `target:model`.** `agent_capacity.provider_key(target, model)`; drain's
bare-agent gate becomes bare-target. A gate on `claude-sub:opus` does not touch
`claude-api:opus`; a provider-wide gate (`claude-sub`) still covers all its models.

**D6 — Auth lanes per harness.** `build_cmd(cell)`; child env built from the cell:
- claude `subscription`: no `--bare`; `ANTHROPIC_API_KEY` removed from the child env so an
  ambient key cannot silently bill the API. claude `api`: `--bare` + the named env var
  copied through (fail loud if unset).
- opencode: model id prefix selects the provider (`opencode/`, `openrouter/`, `google/`);
  credentials come from `auth.json`/env as today; no per-spawn change.
- codex `subscription`: ChatGPT login (verified present). codex `api`: mechanism unverified —
  task 3.6 verifies live whether `-c preferred_auth_method=apikey` (or an equivalent) selects
  the stored key per spawn; until verified the loader rejects a codex `api` target with a
  message naming the task.

**D7 — Retired models.** New failure class `model_unavailable` (24 h default cooldown, env
`GO_AGENT_MODEL_UNAVAILABLE_COOLDOWN` like the others). Sources: (a) `worktrail-routing
--check` compares every opencode cell against `opencode models` and gates missing ids with
this class; (b) `spawn_agent` records it when an opencode `UnknownError` recurs on the same
cell across all retries **and** `--check`-style listing confirms the id is absent (a plain
outage stays `transport`). The dashboard's capacity line names gated cells and the class.
`--check` also warns on effort values outside the harness vocabulary (claude: low/medium/
high/xhigh/max; codex: minimal/low/medium/high/xhigh; opencode: any value, "ignored by the
harness") and on `free`-pool opencode cells whose id lacks a `-free`/`:free` suffix.

**D8 — In-spawn hop on infra failure.** After the primary cell exhausts its `retries` on an
infra failure, `spawn_agent` calls `select_cell` again with the failed cell excluded and
continues in the same call (today only a session-limit notice hops). The task therefore gets
a report-back from the next healthy cell instead of an empty result.

**D9 — Migration is explicit.** Legacy keys (`agents`, `fallback`, `drain.agent`,
`drain.fallback_agents`, `roles.*.agent_cli`, `purpose_tiers`, tiers keyed by harness) raise
`OperatorConfigError` naming `worktrail-routing --migrate`. `--migrate` rewrites the file:
each harness in `fallback` order → a `subscription` target named `<harness>-sub` (opencode →
`opencode-free` with pool `free`), tier cells re-keyed, `agents.<x>.default_model` dropped in
favour of `default_tier: t2-build` (or the row whose cells match), `roles.review` →
`{tier: t1-deep, prefer: <target of its agent_cli>, independent: true}`, `drain` reduced to
`max_workers`. Writes a `.bak` beside the file. No dual-read layer.

**D10 — Front-door session.** Drain's `build_command` and `skill_dispatch` pass `--model`
(and `--effort` where the harness supports it) from `select_cell(roles.front-door.tier or
default_tier)`, so the classification/dispatch session obeys routing like every worker.

**D11 — Starter template.** `--init` writes `claude-sub` + `codex-sub` targets, the four rows
with only those two columns filled, a commented `opencode-free` example, `default_tier:
t2-build`, `roles.review`, and a trailing instruction to run `worktrail-routing --check`. A
test pins that the template passes the loader and contains no `opencode/` model id.

## Risks / Trade-offs

- Breaking config: every machine with a routing.yaml must run `--migrate` once. Mitigated by
  the loud error naming the command and a deterministic rewrite with backup.
- `independent: true` no longer guarantees a different harness when only one is healthy;
  the run record makes that visible. Accepted: a same-harness review beats no review.
- `--check` shells out to `opencode models` (network, ~seconds). Run at drain start and on
  demand, never per spawn.
- Codex API lane ships gated behind verification (D6) rather than on an assumption.

## Open Questions

- Should `purposes` gain a per-repo override (repo-local `routing:` block already wins whole)?
  Not needed for this change; the front-end/back-end/design/explore/categorize entries land in
  the machine-wide file.

## Operator follow-ups (outside this repo)

- **Done (2026-08-28):** ran `worktrail-routing --migrate` against this machine's live
  `~/.worktrail/routing.yaml` (backed up to `routing.yaml.bak`); `worktrail-routing --check`
  now passes with one pre-existing warning (an `opencode-free` t1-deep cell's model id lacks a
  `-free`/`:free` suffix -- not a gate). The operator had already swapped the file's
  `opencode/x-preview-f-free` id for `opencode/muse-spark-1.2-contributor-free` before this
  migration ran (a 2026-08-27 hand-edit, per the file's own comment), so `--check` reported no
  `model_unavailable` gates on this pass -- the retired id named in task 8.2 was already gone
  from the live file by the time `--migrate` shipped.
- **Still open:** `~/bin/worktrail-drain-nightly.sh` (devops repo) hardcodes `--agent`/
  `--fallback-agent` flags. These still work post-migration since `drain.py`'s CLI flags
  continued to accept a harness name, but the script should be updated to name a target
  (e.g. `claude-sub`) once operator habits catch up, per this change's own migration away
  from bare harness identity.
