# Routing config gotchas

Non-obvious interactions between settings, each confirmed against the actual selection code
(`src/worktrail/runtime/selection.py::select_cell`, `src/worktrail/orchestrator/dispatch.py`,
`src/worktrail/orchestrator/live.py`) rather than assumed from the schema alone.

## `independent: true` overrides `prefer`, not the other way around

`select_cell()` builds the candidate order in steps: (1) targets in file order, with `prefer`
moved to the front; (2) drop ineligible `api`-pool targets and targets with no cell in the row;
(3) **if `exclude_harness` is set, partition the already-ordered list into "other harnesses
first, the excluded harness last"** — this step runs *after* `prefer` has already reordered the
list, so it wins.

`independent: true` (the default for the `review` role when unconfigured) sets
`exclude_harness` to whichever harness most recently implemented the task
(`live.py`: `exclude_harness = self.last_agent if independent else None`). If your `prefer`
target shares that harness — e.g. `prefer: claude-sub` while `claude-fable`/`claude-sub` are
both `harness: claude` and Claude did the implementing — **both are pushed to the back**,
regardless of `prefer`. Whatever non-Claude target is left with a cell in that row goes first.

This is the #1 cause of "I only ever configured Claude, why did Codex/OpenCode just get
spawned" — nothing is capacity-gated, nothing is misconfigured; the independent-reviewer
guarantee is doing exactly what it's designed to do. If you only use one implementing harness
and don't want review routed elsewhere, set `roles.review.independent: false`.

## `prefer` reorders a row; it is not a separate fallback list

There is no `routing.fallback` list to also check (that key is retired — see below). The
*entire* fallback chain for a role/tier is that tier row's targets, in `targets:` file order,
with `prefer` (if set) moved to the front. Changing `targets:`' declaration order changes every
tier's fallback order at once.

## Legacy keys fail loud, not silently

`policy.py`'s `_reject_legacy_routing_keys()` raises `OperatorConfigError` (naming
`worktrail-routing --migrate`) for any pre-target-selector shape still present:
`routing.agents`, `routing.fallback`, `routing.drain.agent`, `routing.drain.fallback_agents`,
`routing.purpose_tiers` (renamed `routing.purposes`), or a `routing.tiers` row keyed by bare
harness literal (`claude`/`codex`/`opencode`) instead of a declared `routing.targets` name. A
routing file edited by hand against old documentation, an old example, or an LLM's stale
training data will hit this — the fix is always `worktrail-routing --migrate`, not manually
patching around the error.

## `api`-pool targets need explicit opt-in

A target with `pool: api` (the `openrouter`/`api` literals) is dropped from `select_cell`'s
candidate list unless that same target sets `api_opt_in: true`. This is deliberate — subscription
and free pools are capacity-gated by the harness's own login session; `api` pools bypass that
system and bill per-token, so silently falling back into one would mask real spend. If a target
you added never seems to get selected, check this before assuming it's a capacity gate.

## Effort vocabulary differs per harness

`EFFORT_VOCABULARY` (`policy.py`): `claude` accepts `low`/`medium`/`high`; `codex` additionally
accepts `minimal`; `opencode` has **no** effort vocabulary at all — an `effort:` value on an
`opencode` cell has nothing to translate into (opencode's model variant is the only capability
lever `build_cmd()` maps for it, via `--variant`). Setting `effort` on an opencode cell isn't
wrong, it's just inert.

## A repo-local `routing:` block replaces the machine-wide file, not merges with it

If a repo's own `.worktrail/policy.yaml` declares a non-empty `routing:` block, the machine-wide
`~/.worktrail/routing.yaml` is **not read at all** for that repo — there is no per-key merge.
An edit to the machine-wide file has zero effect on a repo that already has its own `routing:`
block; edit that repo's block instead.

## A stale capacity gate can look like a routing bug

`~/.worktrail/agent-capacity.json` records per-cell availability (`status`, `failure_class`,
`retry_after`). A gate whose `retry_after` has already passed should self-clear on the next
check, but has historically gone stale across a few incidents (see the file's own `audit` log
for past manual clears) and blocked a target that should have been available again. Before
assuming a routing-table edit "didn't take" when a target still isn't being selected, check this
file for that target/model pair's current `status`.

## Already-running processes don't see an edit until restarted

A drain loop or long-lived orchestrator process reads `routing.yaml` once at startup and keeps
it in memory. Editing the file does not affect that process's remaining spawns — restart it, or
wait for the next one-off `/go` invocation, which reads the current file fresh.

## A worker never inherits your user-level `~/.claude/settings.json`

Every `claude` spawn carries `--setting-sources project,local`, which excludes the operator's
user-level settings file. That is intentional and load-bearing: a user-level Stop hook fires on
every worker that commits or writes, forcing an extra continuation turn whose text becomes the
final message the orchestrator parses for its report-back JSON.

The collateral damage is that the file's `env` block never reaches a worker either. A provider
configured *only* there — an alternate `ANTHROPIC_BASE_URL`, its token, model aliases — is
invisible to every worker. The failure is silent and shaped exactly like a successful run: the
worker makes **zero API calls**, produces no output, and still **exits 0** (measured:
`duration_api_ms: 0`, `input_tokens: 0`, `total_cost_usd: 0`, `stop_reason: stop_sequence`).

There is a second, narrower consequence: a spawn launched from an *interactive* session inherits
that session's environment, so it works — while the same cell spawned from a drain, cron, or a
bridge process does not. "It worked when I ran it by hand" is not evidence about the unattended
path.

Do **not** fix this by adding `user` back to `--setting-sources`; that re-opens the Stop-hook
bug. Declare an `env_profiles` entry instead (see `how-to.md`), which copies exactly the keys
you name and fails loudly — before launch — when the source does not match what you asserted.

## `pool: api` adds `--bare`, which skips every `--settings` hook

`--bare` is what forces an `api`-pool claude cell off an ambient subscription login. It also
skips hooks defined in settings — verified live, with a marker hook: a `PreToolUse` hook passed
via `--settings` fires without `--bare` and does **not** fire with it. That includes the worker
worktree guard, which is injected via `--settings` and exists because a worker once wrote into
the canonical checkout.

A profile-backed api cell is the exception: it omits `--bare`, because the injected credentials
already pin the endpoint and auth (which is what `--bare` was doing there), so `--bare` would
buy nothing and cost the guard. An api cell *without* a profile still gets `--bare`, and so still
runs without the guard.

`--bare` also trims plugin/skill context — measured, ~1.3K input tokens versus ~19.8K for the
same prompt. Project-level `CLAUDE.md`/`AGENTS.md` still load either way; it is the plugin
surface that is dropped.
