# drain-operator-config Specification

## Purpose
Gives the operator one machine-wide config file (`worktrail_home()/config.json`) for drain
provider preferences, so config-less manual drains use the operator's stated (e.g. free-tier)
provider instead of a hardcoded paid default, while explicit CLI flags and automation remain
authoritative.
## Requirements
### Requirement: Drain agent selection resolves CLI over config over built-in

`worktrail-drain` SHALL resolve its primary agent as: explicit `--agent` flag, else the
resolved routing table's `drain.agent`, else the first entry of `routing.fallback`; and its
fallback chain as: explicit `--fallback-agent` flags, else `drain.fallback_agents`, else the
remainder of `routing.fallback`. Config-sourced agent names SHALL be validated against the
supported agent set, with a refusal that names the routing file path. The operator config file
is `worktrail_home()/routing.yaml`; `config.json` is no longer read, and there is no
package-resident default agent to fall through to.

#### Scenario: No flags, no config file
- **WHEN** `worktrail-drain` runs with no `--agent` and routing declares no `drain:` block
- **THEN** it SHALL use `routing.fallback`'s order as its primary-plus-fallback chain, rather
  than the previous hardcoded `claude` default

#### Scenario: No flags, config present
- **WHEN** `routing.yaml` declares `drain.agent: opencode` and
  `drain.fallback_agents: [claude, codex]` and no agent flags are passed
- **THEN** the drain SHALL run with `opencode` primary and that fallback chain

#### Scenario: Flags override config
- **WHEN** `--agent codex --fallback-agent claude` is passed alongside a routing drain block
  declaring different agents
- **THEN** the flags SHALL win entirely (config fallbacks are not merged in)

#### Scenario: Config names an unsupported agent
- **WHEN** `drain.agent` is a value outside the supported agent set
- **THEN** the drain SHALL refuse to start (exit 2) with an error naming `routing.yaml`

#### Scenario: No unconfigured machine silently spawns a paid provider
- **WHEN** `worktrail-drain` runs on a machine with no `routing.yaml` at all
- **THEN** it SHALL exit 2 naming the missing file and the `worktrail-routing --init` remedy,
  and SHALL NOT default to any package-resident agent or model

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

