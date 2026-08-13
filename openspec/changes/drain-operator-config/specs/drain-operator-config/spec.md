## Purpose

Gives the operator one machine-wide config file (`worktrail_home()/config.json`) for drain
provider preferences, so config-less manual drains use the operator's stated (e.g. free-tier)
provider instead of a hardcoded paid default, while explicit CLI flags and automation remain
authoritative.

## ADDED Requirements

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
