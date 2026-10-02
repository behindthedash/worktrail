## Context

`_with_default_setting_sources` (`orchestrator/spawnlib.py`) appends `--setting-sources
project,local` to every claude spawn so the operator's USER-level settings file is excluded. The
rationale is recorded in its docstring and is not revisited here. What was not accounted for is
that the settings-file channel is the only carrier of a worker's provider environment: `env` in
`settings.json` is a *setting*, not an ambient variable. Removing the source removes the
environment.

Injection is therefore the correct compensation, and it must happen in Python at spawn time
rather than by relaxing `--setting-sources` — relaxing it re-opens the Stop-hook defect.

Verified live on 2026-10-01 (`claude` 2.1.280):

- `ANTHROPIC_BASE_URL` is **unset** in a plain login shell; `~/.claude/settings.json`'s `env`
  block is the only carrier.
- Clean env + default setting sources → reaches DeepSeek (`duration_api_ms: 3379`, `end_turn`).
- Clean env + `--setting-sources project,local` → `duration_api_ms: 0`, `input_tokens: 0`,
  `total_cost_usd: 0`, `stop_reason: stop_sequence`. Exit 0. No error.
- Injecting the profile's keys under that same argv → `duration_api_ms: 582`, `cost: $0.0051`,
  `models: ['deepseek-flash[1m]']`. The mechanism works, including with `--bare`.

## Goals / Non-Goals

**Goals**

- A target can declare where its harness's environment comes from, generically enough that
  claude+ChatGPT-subscription and claude+OpenRouter need no rework.
- A misconfigured source fails **before launch**, naming what to fix.
- No credential value is ever written to a config file, and none is ever printed for a key the
  operator has not explicitly marked non-secret.

**Non-Goals**

- Reverting or relaxing `--setting-sources project,local`.
- Building the ChatGPT/OpenRouter targets themselves; only the mechanism is in scope.
- Falling through to the next tier-row target on a profile failure (see Decisions).
- Repairing `worktrail-live smoke`, whose pre-existing kwarg defect is tracked separately.

## Decisions

### A profile stores no values, and `keys` is independent of `expect`

`from` is a path, `keys` lists names to copy, `expect` maps a name to the literal it must equal.
Because only names are stored, a profile can live in the operator's routing file without ever
holding a credential.

The two axes are deliberately independent: a key may be **asserted without being copied**
(provenance checking — "this file is the DeepSeek settings file"), and **copied without being
asserted** (its value then can never appear in an error message). This is what makes the
never-print rule mechanically easy to honour rather than a discipline to remember: a message may
name a value only for a key in `expect`.

### Applied before the harness/pool branch, harness-generic

`_apply_env_profile` runs at the top of `build_child_env`, before the claude-specific logic.

Ordering matters: applying *after* would let a profile re-add `ANTHROPIC_API_KEY` on a
`subscription` cell **after** that lane pops it, silently defeating the documented guarantee that
an ambient key cannot switch a subscription spawn's billing to the API. Applying before means the
pop still wins. Placement also makes the mechanism harness-generic at no extra cost — the smaller
diff is the more general one here.

### The profile table is threaded, never re-loaded

`build_child_env` receives only a `Cell`, and `Cell.auth` carries just `{"profile": name}`. The
table is passed from `_prepare_child_env`, where the same resolved routing dict `select_cell`
served from is already in scope. Re-loading policy inside `build_child_env` would add I/O on every
session-limit and infra hop, and could diverge from the table that chose the cell.

### A new module, not `spawnlib` or `policy`

`routing_cli` imports `..orchestrator.agent_capacity`, and `spawnlib` imports
`..router.routing_cli`; a module-level `routing_cli -> spawnlib` edge is an import cycle.
`policy.py` is also wrong: its validator must never open a profile file, so reads belong at spawn
time and at `--check` time, not on every `load_policy()`.

### Hard-fail at spawn; FAIL at `--check`; no capacity gate

Failures raise `OperatorConfigError`. `--check` marks the cell `FAIL` and exits non-zero, but
deliberately does **not** call `agent_capacity.record()`. A recorded gate is skipped *silently* by
`select_cell` on the next spawn, converting a loud configuration error into an invisible fallback
to the next rung — precisely the failure class this change removes. Gates model provider
conditions; this is operator configuration with an `auth` cooldown that would outlive the fix.

Graceful fall-through to the next target was considered and rejected: it would push file-reading
into `selection.py`, which is deliberately pure (it refuses even to import the capacity exception
class).

### `--bare` is omitted for profile-backed cells

`--bare` forces the `api` lane off an ambient subscription login, but it also skips every
settings-injected hook. Verified with a marker hook: a `PreToolUse` hook passed via `--settings`
fires without `--bare` and does not fire with it. The worktree guard is injected that way, so
every claude `pool: api` spawn today runs unguarded — and a profile-backed target would be the
first to actually use that lane. Injected credentials already pin the endpoint and auth, which is
all `--bare` was doing there, so omitting it costs nothing and restores the guard.

Note `--bare` does **not** disable project `CLAUDE.md`/`AGENTS.md` (verified with a marker: the
value was returned both ways). It trims plugin/skill context only — ~1.3K input tokens against
~19.8K for the same prompt.

### `explicit_cell_override` must reproduce the whole target

Its throwaway routing file **replaces** the operator's file for the duration of an override, so
anything it omits is genuinely absent downstream, not inherited. Carrying only harness/pool made
an api target unreachable (`select_cell` skips it for missing `api_opt_in`, then `build_child_env`
would have raised for missing `auth`). It now writes `api_opt_in`, the full `auth` mapping, and
the one `env_profiles` entry `auth.profile` names — via `yaml.safe_dump`, because a model id like
`deepseek-flash[1m]` is not safe to interpolate into a plain scalar (`[` opens a flow sequence
inside `{}`). Only paths and key names are written, never values, so the 0600 mkstemp file stays
secret-free.

## Migration

The pre-change `_validate_routing` silently ignores unknown top-level routing keys. Landing the
`~/.worktrail/routing.yaml` edit **before** this code therefore drops `env_profiles` with no
warning and spawns the target with no injected environment — reproducing the original zero-token
no-op behind a configuration file that looks correct. Code first, then config.

## Risks

- `auth.profile` + `auth.env` on one target is a new hard failure. No existing target sets both;
  `worktrail-routing --check` over the current file is the regression check.
- A profile that copies a token but not `ANTHROPIC_BASE_URL` sends it to real Anthropic. Loud, but
  expensive to diagnose; the canonical example in the docs pairs them and asserts the base URL.
- The profile file is re-read on every session-limit/infra hop. A broken file therefore raises
  instead of hopping — intended, and worth stating.
