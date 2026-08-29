## MODIFIED Requirements

### Requirement: Harness auth follows the target's pool
`build_cmd` and the child environment SHALL be built from the selected cell. For a claude
`subscription` target the spawn SHALL omit `--bare` and SHALL remove `ANTHROPIC_API_KEY` from
the child environment; for a claude `api` target the spawn SHALL pass `--bare` and copy the
env var named in `auth.env` into the child environment, failing loud before launch when it
is unset. For a codex `subscription` target the spawn SHALL use the existing isolated
Worktrail Codex home with the operator's ChatGPT login inherited, unchanged. For a codex
`api` target the spawn SHALL set `CODEX_HOME` to the path named in `auth.codex_home` — a
home the operator provisioned with `codex login --with-api-key` — and SHALL NOT inherit the
parent home's ChatGPT auth; it SHALL fail loud before launch when `auth.codex_home` is
undeclared or the declared home contains no `auth.json` (checked by existence only, never
read). This supersedes the interim rule that a codex `api` target be rejected by the
loader: its per-spawn auth selection is live-verified (routing-target-selector task 3.6,
codex-cli 0.149.1 — env-var and config-field selectors are inert; `CODEX_HOME` isolation
is the working mechanism).

#### Scenario: Ambient API key never bills a subscription lane
- **WHEN** the operator's shell exports `ANTHROPIC_API_KEY` and a `claude-sub` cell is spawned
- **THEN** the child process environment SHALL NOT contain `ANTHROPIC_API_KEY` and the
  command SHALL NOT include `--bare`

#### Scenario: API lane forces key auth
- **WHEN** a `claude-api` cell with `auth: {env: ANTHROPIC_API_KEY}` is spawned
- **THEN** the command SHALL include `--bare` and the child environment SHALL carry the key

#### Scenario: Codex api lane spawns in its declared home without ChatGPT auth
- **WHEN** a codex cell with `pool: api` and `auth: {codex_home: <provisioned path>}` is
  spawned and that path contains an `auth.json`
- **THEN** the child environment SHALL carry `CODEX_HOME=<that path>` and the parent
  home's ChatGPT credentials SHALL NOT be copied into it

#### Scenario: Codex api lane without a declared home fails loud
- **WHEN** a codex cell with `pool: api` and no `auth.codex_home` is spawned
- **THEN** the spawn SHALL raise an operator-config error naming the target and the
  `auth: {codex_home: <path>}` remedy, before any process is launched

#### Scenario: Codex api lane with an unprovisioned home fails loud
- **WHEN** a codex cell with `pool: api` declares an `auth.codex_home` whose directory has
  no `auth.json`
- **THEN** the spawn SHALL raise an operator-config error naming the path and the
  `codex login --with-api-key` provisioning step, before any process is launched

#### Scenario: Codex subscription lane is unchanged
- **WHEN** a codex cell with `pool: subscription` is spawned
- **THEN** the child environment SHALL be prepared exactly as before this change (isolated
  Worktrail home, ChatGPT auth inherited)
