# drain-operator-config Specification

## Purpose
Gives the operator one machine-wide config file (`worktrail_home()/config.json`) for drain
provider preferences, so config-less manual drains use the operator's stated (e.g. free-tier)
provider instead of a hardcoded paid default, while explicit CLI flags and automation remain
authoritative.
## Requirements
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

### Requirement: A malformed operator config fails loud

Loading the operator config SHALL treat a missing `drain:` block as an empty drain config, and
SHALL raise — never silently ignore — a routing file that is unreadable, is not valid YAML, is
not a mapping, or whose `drain` section has the wrong shape (`agent` not a string,
`fallback_agents` not a list of strings, `max_workers` not a positive integer).
`worktrail-drain` SHALL surface that error and exit 2 rather than proceeding with built-in
defaults.

#### Scenario: Config file contains invalid JSON
- **WHEN** `routing.yaml` exists but does not parse as YAML (the successor to the former
  `config.json` invalid-JSON case)
- **THEN** `worktrail-drain` SHALL exit 2 with an error naming the file, and no drain runs

#### Scenario: Malformed drain section
- **WHEN** `routing.yaml` declares `drain: {max_workers: "two"}`
- **THEN** `worktrail-drain` SHALL exit 2 with an error naming the file and the offending key

#### Scenario: Absent drain section is not an error
- **WHEN** `routing.yaml` parses cleanly but declares no `drain:` block
- **THEN** loading SHALL succeed and the drain SHALL fall through to `routing.fallback`

### Requirement: Drain worker count resolves CLI over config over built-in

`worktrail-drain` SHALL resolve its worker-slot count as: explicit `--max-workers` flag, else
the resolved routing table's `drain.max_workers`, else a built-in default of `2`, following the
same CLI-over-config-over-built-in precedence already used for `drain.agent` and
`drain.fallback_agents`. A built-in numeric default is retained here (unlike agent/model
resolution) because a worker count carries no provider or cost intent that could silently
diverge from the operator's stated preference.

#### Scenario: No flag, no config file
- **WHEN** `worktrail-drain` runs with no `--max-workers` and routing declares no
  `drain.max_workers`
- **THEN** it SHALL resolve a worker-slot count of `2`

#### Scenario: No flag, config present
- **WHEN** `routing.yaml` declares `drain.max_workers: 3` and no `--max-workers` flag is passed
- **THEN** the drain SHALL resolve a worker-slot count of `3`

#### Scenario: Flag overrides config
- **WHEN** `--max-workers 1` is passed alongside routing declaring `drain.max_workers: 3`
- **THEN** the flag SHALL win and the drain SHALL resolve a worker-slot count of `1`

#### Scenario: Config names an invalid worker count
- **WHEN** `drain.max_workers` is present but is not a positive integer
- **THEN** the drain SHALL refuse to start (exit 2) with an error naming `routing.yaml`

