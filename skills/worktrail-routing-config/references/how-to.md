# Routing config how-tos

All edits below are to `~/.worktrail/routing.yaml` (or the repo-local `routing:` block, which
uses the same schema one level deeper under a `routing:` key). Run `worktrail-routing --check`
after every edit.

## Add a new harness/adapter

Add an entry under `targets:` naming a supported harness (`claude`, `codex`, or `opencode`) and
a pool (`subscription`, `free`, or `api`; `api` also needs `api_opt_in: true` on the same
target to actually be selected — see gotchas.md). Then give it at least one cell in whichever
tier(s) it should serve:

```yaml
targets:
  opencode-free:
    harness: opencode
    pool: free
tiers:
  t2-build:
    opencode-free:
      model: opencode/some-model
```

A target with no cell in a tier's row simply can't serve that tier — no error, it's skipped.

## Disable a harness/adapter without deleting its config

Two options, depending on whether you want it gone or just parked:

- **Remove its cells from every tier row it appears in**, but leave the `targets:` entry. It
  stays declared (so re-enabling is a one-line uncomment) but is never selected, since
  `select_cell` skips any target with no cell in the row it's walking.
- **Delete the `targets:` entry entirely** if you also want `routing.roles`/`prefer` references
  to it to warn/fail loudly instead of silently resolving elsewhere.

Don't try to "disable" a harness by leaving its login/auth broken — that produces confusing
capacity-gate churn in `agent-capacity.json` instead of a clean absence.

## Add a new model to an existing harness (e.g. add a DeepSeek model to `opencode`)

`opencode`'s models are just model strings — there's no separate catalog file. Point an
existing `opencode-*` target's cell (or a new tier) at the new model string:

```yaml
tiers:
  t2-build:
    opencode-free:
      model: opencode/deepseek-v4-flash
```

If you want the DeepSeek model available at a different capability tier than whatever
`opencode-free` currently serves, either change that cell's model, or add a second
`opencode-*` target (see "Add a new harness" above) so the two models coexist as an
intra-harness ladder — same pattern the starter config uses for `claude-fable`/`claude-sub`.

## Point a harness at a custom Anthropic-compatible endpoint with an env profile

Use this when the `claude` CLI itself must be aimed somewhere other than Anthropic — DeepSeek,
OpenRouter, or any other Anthropic-compatible gateway. Configure the endpoint in a JSON file
(commonly `~/.claude/settings.json`, whose `env` block is what an interactive session uses), then
declare a profile that copies the relevant keys onto the worker:

```yaml
env_profiles:
  claude-deepseek:
    from: ~/.claude/settings.json     # required; must be absolute or start with ~
    keys:                             # copied into the worker's environment
      - ANTHROPIC_BASE_URL
      - ANTHROPIC_AUTH_TOKEN
      - ANTHROPIC_MODEL
      - ANTHROPIC_DEFAULT_OPUS_MODEL
      - ANTHROPIC_DEFAULT_SONNET_MODEL
      - ANTHROPIC_DEFAULT_HAIKU_MODEL
      - CLAUDE_CODE_SUBAGENT_MODEL
    expect:                           # asserted before launch
      ANTHROPIC_BASE_URL: https://api.deepseek.com/anthropic

targets:
  claude-deepseek:
    harness: claude
    pool: api
    api_opt_in: true
    auth:
      profile: claude-deepseek

tiers:
  t2-build:
    claude-deepseek:
      model: deepseek-flash[1m]
```

Then `worktrail-routing --check` and confirm the cell's NOTES column reads
`env profile claude-deepseek ok`.

Things worth knowing before you write one:

- **A profile stores no values** — a path, key *names*, and an optional non-secret assertion. The
  `expect` value is the one value that may appear in an error message, because writing it is what
  declares that key non-secret. Never put a credential in `expect`, `keys`, or `from`.
- **`keys` and `expect` are independent.** Assert a key without copying it (provenance checking),
  or copy one without asserting (its value then never becomes printable).
- **`auth.profile` and `auth.env` are mutually exclusive** — they name two sources for one auth
  lane. A target declaring both fails at spawn and warns in `--check`.
- **Copy `ANTHROPIC_BASE_URL` whenever you copy a token.** A profile that injects a DeepSeek token
  but not the base URL sends that token to real Anthropic — a loud failure, but an expensive one
  to diagnose.
- **The model id needs quoting in flow style.** `{model: deepseek-flash[1m]}` is a YAML parse
  error (`[` opens a flow sequence inside `{}`). Use block style, as above, or quote it.
- **`pool: api` + a profile does not get `--bare`.** A profile-backed api cell omits it, because
  `--bare` skips every `--settings`-injected hook — including the worktree guard. The injected
  credentials already pin the endpoint, which is what `--bare` was there for.

## Add or remove a tier

Add a new top-level key under `tiers:` with a cell per target that should serve it, then
reference it from `default_tier`, a `roles.<role>.tier`, or a `purposes` entry. Removing a
tier that's still referenced elsewhere leaves those references pointing at a nonexistent row —
`worktrail-routing --check` catches this.

## Route a purpose to a tier

```yaml
purposes:
  security-review: t1-deep
  bulk-mechanical: t3-bulk
```

Only consulted for `implement`/`fix`/`cleanup` roles, and only for a task that carries that
`purpose` in its frontmatter — judgment roles (`review`/`resolve`/`ci-fix`/`assembly-resolve`)
never consult `purposes` at all (see gotchas.md).

## Pin, change, or unpin the code reviewer

```yaml
roles:
  review:
    tier: t1-deep
    prefer: claude-sub     # optional: which target to try first within that tier's row
    independent: true      # optional: exclude whichever harness implemented the task (default when unconfigured)
```

- To force review onto a specific target regardless of who implemented, set `independent:
  false` — this is also the fix if you only use one harness to implement and don't want
  `independent`'s exclusion silently pushing review onto a different harness (see gotchas.md).
- To remove the override entirely, delete the `roles.review` block — review then defaults to
  `{tier: t1-deep (or default_tier if t1-deep isn't declared), independent: true}`.
- `resolve`/`ci-fix`/`assembly-resolve` take the identical `{tier, prefer?, independent?}` shape
  under their own `roles.<name>` key.

## Change `default_tier`

```yaml
default_tier: t2-build
```

This is the tier used for any role/task that doesn't resolve through a more specific path
(explicit per-task `tier`, judgment-role `roles.<role>.tier`, `purposes` match, or matching
`complexity` row). Must name a tier that's actually declared under `tiers:`, or it's ignored
with a warning and effectively unset.

## Validate a change before trusting it

```bash
worktrail-routing --check                                        # syntax + legacy-key + cell sanity + spawn readiness
worktrail-policy --repo <repo-path> --resolve-routing "x:x" --json  # print the fully resolved table
```

`--resolve-routing`'s `"x:x"` argument is vestigial (ignored) — it always returns the complete
`{targets, tiers, roles, purposes, default_tier, env_profiles, drain}` table, which is what to read to confirm
an edit resolved the way you expect before it affects a live spawn.

## Read a readiness failure from `worktrail-routing --check`

`--check` is a spawn-readiness probe, not just a schema linter: it reshapes the file through the
same resolver a spawn uses, then builds every declared cell's command and child environment from
that *resolved* table. So a cell whose auth lane can't be assembled fails the check — marked
`FAIL` in the STATUS column, its message on stderr, exit code non-zero — even though the YAML
itself is perfectly valid. Find the message's class below and fix the target it names; each one
is deterministic in the current file/environment, so re-running only reproduces it.

- **`... declares auth.profile '<name>', which is not declared in routing.env_profiles ...`** —
  the target points at a profile this routing file doesn't define. `env_profiles:` resolves
  alongside `targets:` in the same file, so remember a repo-local `routing:` block replaces the
  machine-wide file entirely: a profile that exists in `~/.worktrail/routing.yaml` is absent for
  a repo that declares its own block. Fix: declare `env_profiles: {<name>: {from: ..., keys:
  [...]}}` there (see "Point a harness at a custom Anthropic-compatible endpoint" above).
- **`... env profile '<name>': ...`** — the profile is declared but can't be resolved against
  its source: a missing/relative `from`, an unreadable or non-JSON file with no `env` object, a
  key that is missing, empty, or not a string, or an `expect` mismatch. The message names the key
  (never an unasserted value) and the file. Fix the source file or the profile's `keys`/
  `expect`; don't loosen `expect` just to make it pass.
- **`... requires <VAR> to be set in the environment for its 'api' pool ...`** — the target
  declares `auth: {env: <VAR>}` but `<VAR>` is unset or empty in the process running the check.
  Fix: export it in the *spawning* environment — not necessarily the shell you type in, since a
  drain under cron/systemd inherits that job's environment, and "it worked by hand" says nothing
  about the unattended path (see gotchas.md). The routing file stores no value here; `auth.env`
  names a variable and nothing more.
- **`... has no auth.env or auth.profile configured ...`** (and its mirror, `... declares both
  auth.env and auth.profile ...`) — a claude `api` cell has zero or two auth sources. Fix: give
  it exactly one.
- **A cell reported unready for `api_opt_in`** — the target is `pool: api` without
  `api_opt_in: true`. Fix: add `api_opt_in: true` to its `targets:` entry (or move it off the
  `api` pool). Without it `select_cell` silently drops the target from every row, so this is the
  probe catching "my target is just never selected" before it wastes an afternoon.
- **`... (harness codex, pool api) has no auth.codex_home configured ...`** — a codex `api` cell
  spawns in its own pre-provisioned home and deliberately does not inherit the parent's ChatGPT
  login. Fix: add `auth: {codex_home: <path>}` naming the home.
- **`...'s auth.codex_home (<path>) has no auth.json ...`** — the declared home exists but was
  never logged in. The probe checks this; it never creates the home. Fix: provision it once with
  `CODEX_HOME=<path> codex login --with-api-key`.
- **A harness the probe reports as unsupported** — `harness:` must be one of `claude`, `codex`,
  `opencode`. Fix: correct the value.

**None of these is a capacity gate, and waiting one out is not a thing.** A capacity gate is a
note in `~/.worktrail/agent-capacity.json` with a `retry_after`, modeling a *provider* condition
(a rate limit, a retired model); `select_cell` walks silently past a gated cell to the next rung,
which is why a capacity-gated cell does not stop a drain. A readiness failure is operator config
or a missing variable instead: nothing is recorded in `agent-capacity.json`, there is no
`retry_after` to wait out, and it is deliberately not gated, because turning a loud config error
into an invisible fallback is the exact failure mode `env_profiles` exist to eliminate. A drain
that hits one refuses to start (exit 2, naming the cell and the routing file) rather than routing
around it. Fix the file or export the variable, re-run `--check`, then let the drain start.
