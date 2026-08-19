## ADDED Requirements

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
