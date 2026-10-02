## ADDED Requirements

### Requirement: A worker environment can be supplied from a declared profile without storing values

The routing table SHALL accept an `env_profiles` section mapping a profile name to
`{from, keys, expect?}`, where `from` names a JSON file containing an `env` object, `keys` lists
the key names to copy from that object, and `expect` optionally maps a key name to the literal
value it must equal. A profile SHALL NOT store any value.

A target MAY name a profile through `auth.profile`. Before launching a worker the system SHALL
resolve that profile, assert every `expect` entry against the file, and copy every `keys` entry
into the child environment — before the harness/pool branch, so the claude `subscription` lane's
key removal still applies. It SHALL do so using the same resolved routing table the cell was
selected from, without re-reading policy.

The system SHALL raise an operator-config error naming the target, the profile, the resolved file
and the offending key when the file is missing or unreadable, is not valid JSON, has no `env`
object, omits a declared key, has an empty or non-string value for one, or fails an `expect`
assertion. An error message SHALL NOT contain a value for any key not named in `expect`. The
system SHALL NOT launch a process when any of these fail.

`keys` and `expect` SHALL be independent: a key MAY be asserted without being copied, and copied
without being asserted.

#### Scenario: Declared keys reach the child environment

- **WHEN** a target declares `auth.profile: deepseek` and `env_profiles.deepseek` names a
  readable file with `keys: [ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN]`
- **THEN** the child environment SHALL contain both keys with the file's values

#### Scenario: A configured endpoint is proven before launch

- **WHEN** `env_profiles.deepseek.expect` asserts `ANTHROPIC_BASE_URL` and the file holds a
  different value
- **THEN** the spawn SHALL raise an operator-config error naming the key, the expected value and
  the file's actual value, before any process is launched

#### Scenario: A missing key fails loud

- **WHEN** a profile lists a key under `keys` that the source file's `env` object does not
  contain
- **THEN** the spawn SHALL raise an operator-config error naming the key and the file, before
  any process is launched

#### Scenario: A secret outside expect is never printed

- **WHEN** a profile's source file holds a credential under a key not named in `expect` and any
  resolution failure occurs
- **THEN** the raised error's message SHALL NOT contain that value

#### Scenario: An undeclared profile is a configuration error

- **WHEN** a target names an `auth.profile` that `env_profiles` does not declare
- **THEN** the spawn SHALL raise an operator-config error naming the target, the profile and the
  routing file, before any process is launched

#### Scenario: A profile applies to any harness

- **WHEN** a non-claude target declares `auth.profile`
- **THEN** its declared keys SHALL still be copied into the child environment

### Requirement: Profile and named-variable auth are mutually exclusive alternatives

A target's `auth` SHALL supply at most one of `env` and `profile`. When both are declared the
spawn SHALL raise an operator-config error before launching, and the routing table validator
SHALL surface a warning naming the target. `auth.codex_home` SHALL NOT be treated as conflicting
with `profile`, since it selects a home rather than supplying credentials.

A profile MAY be declared on a `subscription` target, but the routing validator SHALL warn,
because that lane removes `ANTHROPIC_API_KEY` after injection and a profile-supplied key
therefore does not survive.

#### Scenario: Both lanes declared fails loud

- **WHEN** a target declares `auth: {env: ANTHROPIC_API_KEY, profile: deepseek}`
- **THEN** the spawn SHALL raise an operator-config error naming the target, before any process
  is launched

#### Scenario: A profile supplies the api lane's credentials alone

- **WHEN** a claude `api` target declares `auth.profile` and no `auth.env`
- **THEN** the spawn SHALL NOT require `auth.env`, and SHALL NOT raise for its absence

#### Scenario: The profile is applied before the subscription key removal

- **WHEN** a `subscription` target's profile declares `ANTHROPIC_API_KEY`
- **THEN** the child environment SHALL NOT contain `ANTHROPIC_API_KEY`

### Requirement: A subscription spawn cannot be redirected by an ambient provider environment

A claude `subscription` spawn SHALL remove every variable that would redirect it to a different
endpoint, token, or model than the subscription it was selected for — not only
`ANTHROPIC_API_KEY` but also `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, the
`ANTHROPIC_DEFAULT_*_MODEL` aliases, and `CLAUDE_CODE_SUBAGENT_MODEL`. Worker environments are
built from the spawning process's environment, and an interactive session carries exactly those,
so a subscription cell selected because its tier wanted Anthropic would otherwise be silently
sent to the ambient endpoint instead.

#### Scenario: An ambient base URL does not hijack a subscription cell

- **WHEN** the spawning process's environment carries `ANTHROPIC_BASE_URL` and
  `ANTHROPIC_AUTH_TOKEN` and a `claude-sub` cell is spawned
- **THEN** the child environment SHALL contain neither

#### Scenario: Unrelated ambient variables survive

- **WHEN** a `claude-sub` cell is spawned from an environment carrying unrelated variables
- **THEN** those SHALL be preserved in the child environment

### Requirement: A profile-backed api cell keeps its settings-injected hooks

A claude `api` cell that declares `auth.profile` SHALL NOT be spawned with `--bare`. `--bare`
skips hooks defined in settings, which includes the worktree guard injected through `--settings`;
the profile's injected credentials already pin the endpoint and auth, which is the only purpose
`--bare` serves for that lane. An `api` cell without a profile SHALL continue to receive
`--bare`.

#### Scenario: Profile-backed api cell omits bare and keeps its guard

- **WHEN** a claude `api` cell declaring `auth.profile` is spawned
- **THEN** the command SHALL NOT include `--bare` and SHALL include `--settings`

#### Scenario: An api cell without a profile is unchanged

- **WHEN** a claude `api` cell declares `auth.env` and no `auth.profile` is spawned
- **THEN** the command SHALL include `--bare`

### Requirement: An explicit override reproduces the target's whole definition

The temporary routing file an explicit model or effort override writes SHALL reproduce the
selected target's complete definition — its harness, pool, `api_opt_in`, `auth`, and the one
`env_profiles` entry that `auth.profile` names. That file replaces the operator's routing file
for the duration of the override, so anything it omits is absent downstream rather than
inherited; omitting any of these makes an api- or profile-backed target unreachable through the
explicit-override path. The file SHALL continue to contain only paths and key names, never a
value.

#### Scenario: An api target survives an explicit model override

- **WHEN** an explicit override names a target whose `pool` is `api` with `api_opt_in` and
  `auth.profile` declared
- **THEN** the temporary routing file SHALL carry `api_opt_in`, the `auth` mapping, and the
  referenced `env_profiles` entry, and the target SHALL remain selectable

#### Scenario: A bracketed model id round-trips

- **WHEN** an explicit override requests a model id containing YAML flow indicators
- **THEN** the temporary routing file SHALL parse back to that exact model id

## MODIFIED Requirements

### Requirement: Harness auth follows the target's pool

`build_cmd` and the child environment SHALL be built from the selected cell. For a claude
`subscription` target the spawn SHALL omit `--bare` and SHALL remove every provider-redirect
variable from the child environment (see "A subscription spawn cannot be redirected by an ambient
provider environment"); for a claude `api` target the spawn SHALL pass `--bare` unless the target
declares `auth.profile`, and SHALL obtain its credentials from either the env var named in
`auth.env` or the profile named in `auth.profile`, failing loud before launch when neither is
configured or the declared one cannot be resolved. For a codex `subscription` target the spawn
SHALL use the existing isolated Worktrail Codex home with the operator's ChatGPT login inherited,
unchanged. For a codex `api` target the spawn SHALL set `CODEX_HOME` to the path named in
`auth.codex_home` — a home the operator provisioned with `codex login --with-api-key` — and SHALL
NOT inherit the parent home's ChatGPT auth; it SHALL fail loud before launch when
`auth.codex_home` is undeclared or the declared home contains no `auth.json` (checked by
existence only, never read). This supersedes the interim rule that a codex `api` target be
rejected by the loader: its per-spawn auth selection is live-verified (routing-target-selector
task 3.6, codex-cli 0.149.1 — env-var and config-field selectors are inert; `CODEX_HOME`
isolation is the working mechanism).

#### Scenario: Ambient API key never bills a subscription lane

- **WHEN** the operator's shell exports `ANTHROPIC_API_KEY` and a `claude-sub` cell is spawned
- **THEN** the child process environment SHALL NOT contain `ANTHROPIC_API_KEY` and the
  command SHALL NOT include `--bare`

#### Scenario: API lane forces key auth

- **WHEN** a `claude-api` cell with `auth: {env: ANTHROPIC_API_KEY}` and no `auth.profile` is
  spawned
- **THEN** the command SHALL include `--bare` and the child environment SHALL carry the key

#### Scenario: API lane resolves a profile

- **WHEN** a `claude-api` cell with `auth: {profile: deepseek}` is spawned and that profile
  resolves
- **THEN** the command SHALL omit `--bare` and the child environment SHALL carry the profile's
  declared keys

#### Scenario: API lane with neither auth source fails loud

- **WHEN** a claude cell with `pool: api` declares neither `auth.env` nor `auth.profile`
- **THEN** the spawn SHALL raise an operator-config error naming the target and both remedies,
  before any process is launched

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
