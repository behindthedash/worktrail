# drain-operator-config Specification

## Purpose
Gives the operator one machine-wide config file (`worktrail_home()/config.json`) for drain
provider preferences, so config-less manual drains use the operator's stated (e.g. free-tier)
provider instead of a hardcoded paid default, while explicit CLI flags and automation remain
authoritative.
## Requirements
### Requirement: Drain agent selection resolves CLI over config over built-in

`worktrail-drain` SHALL resolve its primary agent as: explicit `--agent` flag, else the operator
config's `drain.agent`, else `claude`; and its fallback chain as: explicit `--fallback-agent`
flags, else the operator config's `drain.fallback_agents`, else empty. Config-sourced agent
names SHALL be validated against the supported agent set, with a refusal that names the config
file path.

#### Scenario: No flags, no config file
- **WHEN** `worktrail-drain` runs with no `--agent` and no `config.json`
- **THEN** it uses `claude` with no fallbacks, exactly as before

#### Scenario: No flags, config present
- **WHEN** `config.json` declares `drain.agent: opencode` and
  `drain.fallback_agents: [claude, codex]` and no agent flags are passed
- **THEN** the drain runs with `opencode` primary and that fallback chain

#### Scenario: Flags override config
- **WHEN** `--agent codex --fallback-agent claude` is passed alongside a config declaring
  different agents
- **THEN** the flags win entirely (config fallbacks are not merged in)

#### Scenario: Config names an unsupported agent
- **WHEN** `drain.agent` is a value outside the supported agent set
- **THEN** the drain refuses to start (exit 2) with an error naming the config file

### Requirement: A malformed operator config fails loud

Loading the operator config SHALL treat a missing file as an empty config, and SHALL raise —
never silently ignore — a file that is unreadable, is not valid JSON, is not a JSON object, or
whose `drain` section has the wrong shape. `worktrail-drain` SHALL surface that error and exit 2
rather than proceeding with built-in defaults.

#### Scenario: Config file contains invalid JSON
- **WHEN** `config.json` exists but does not parse
- **THEN** `worktrail-drain` exits 2 with an error naming the file, and no drain runs

### Requirement: Drain worker count resolves CLI over config over built-in

`worktrail-drain` SHALL resolve its worker-slot count as: explicit `--max-workers` flag, else
the operator config's `drain.max_workers`, else a built-in default of `2`, following the same
CLI-over-config-over-built-in precedence already used for `drain.agent` and
`drain.fallback_agents`.

#### Scenario: No flag, no config file
- **WHEN** `worktrail-drain` runs with no `--max-workers` and no `config.json`
- **THEN** it resolves a worker-slot count of `2`

#### Scenario: No flag, config present
- **WHEN** `config.json` declares `drain.max_workers: 3` and no `--max-workers` flag is passed
- **THEN** the drain resolves a worker-slot count of `3`

#### Scenario: Flag overrides config
- **WHEN** `--max-workers 1` is passed alongside a config declaring `drain.max_workers: 3`
- **THEN** the flag wins and the drain resolves a worker-slot count of `1`

#### Scenario: Config names an invalid worker count
- **WHEN** `drain.max_workers` is present but is not a positive integer
- **THEN** the drain refuses to start (exit 2) with an error naming the config file

