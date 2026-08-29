## MODIFIED Requirements

### Requirement: Drain agent selection resolves CLI over config over built-in
`worktrail-drain` SHALL derive its candidate order from the routing table's `targets`
(file order) via `select_cell(default_tier)` on every iteration; explicit `--agent` /
`--fallback-agent` flags SHALL still win entirely when passed, interpreted as target names.
The `drain:` block SHALL carry no `agent` or `fallback_agents` keys (their presence fails
loud naming `worktrail-routing --migrate`). The front-door session SHALL be spawned with the
selected cell's model (and effort where the harness supports it). Before the first
iteration the drain SHALL run the routing liveness check (`worktrail-routing --check`
semantics) alongside `validate_agent_runtime`, so a retired model is gated before any
launch. The operator config file is `worktrail_home()/routing.yaml`; there is no
package-resident default target to fall through to.

#### Scenario: No flags, no config file
- **WHEN** `worktrail-drain` runs with no `--agent` and routing declares no `drain:` block
- **THEN** it SHALL use `targets` file order via `select_cell(default_tier)` as its
  primary-plus-fallback chain, rather than any hardcoded default

#### Scenario: No flags, config present
- **WHEN** `routing.yaml` declares targets `claude-sub, codex-sub, opencode-free` in that
  order and `default_tier: t2-build`, and no agent flags are passed
- **THEN** the drain SHALL try `claude-sub`'s `t2-build` cell first and degrade in target
  order, passing that cell's model to the front-door session

#### Scenario: Flags override config
- **WHEN** `--agent codex-sub --fallback-agent claude-sub` is passed
- **THEN** the flags SHALL win entirely (routing order is not merged in)

#### Scenario: Legacy drain agent keys are refused
- **WHEN** `routing.yaml` still declares `drain.agent` or `drain.fallback_agents`
- **THEN** the drain SHALL refuse to start (exit 2) naming the key and
  `worktrail-routing --migrate`

#### Scenario: Config names an unsupported agent
- **WHEN** `--agent` names a target not declared in `targets`, or a target's `harness` is
  outside the supported harness set
- **THEN** the drain SHALL refuse to start (exit 2) with an error naming `routing.yaml`

#### Scenario: Retired model is gated before launch
- **WHEN** a `default_tier` cell names an opencode model absent from `opencode models`
- **THEN** the preflight SHALL gate that cell with `model_unavailable` and the first
  iteration SHALL select the next ungated cell instead of launching and failing

#### Scenario: No unconfigured machine silently spawns a paid provider
- **WHEN** `worktrail-drain` runs on a machine with no `routing.yaml` at all
- **THEN** it SHALL exit 2 naming the missing file and the `worktrail-routing --init` remedy,
  and SHALL NOT default to any package-resident target or model
