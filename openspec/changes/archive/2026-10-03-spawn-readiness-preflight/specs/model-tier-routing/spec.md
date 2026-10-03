## ADDED Requirements

### Requirement: The routing check proves spawn readiness against the resolved table

`worktrail-routing --check` SHALL decide a tier cell's status from the **resolved** routing
table — `load_policy()` followed by `resolve_routing()`, the same values `select_cell()` and
`build_child_env()` consume — and never from the raw routing mapping the loader validated. For
every tier cell whose target is declared, the check SHALL construct that cell's spawn: its
launcher command and its child environment, built with the resolved table's `env_profiles`,
against the checking process's own environment.

A cell whose construction raises, or whose target the selector can never serve, SHALL be
reported `FAIL` with the raised message and SHALL flip the exit code. As with an unresolvable
env profile, a readiness `FAIL` SHALL NOT be recorded as an `agent_capacity` gate.

The check SHALL report `FAIL` for an `api`-pool target that declares a tier cell but no
`api_opt_in`, naming the target and the remedy, because the selector drops such a target from
every row it appears in.

A claude `api` cell whose `auth.env` variable is unset in the checking process's environment
SHALL be reported as unready rather than `ok`: no spawn built from that environment can
authenticate it.

#### Scenario: A key the resolver drops is caught even though the file declares it

- **WHEN** the routing file declares `env_profiles` and a target whose `auth.profile` names one
  of them, but the resolved table omits `env_profiles`
- **THEN** `--check` SHALL report that target's cells `FAIL`, naming the target and the profile,
  and SHALL exit non-zero

#### Scenario: An api target that can never be selected is caught

- **WHEN** a target declares `pool: api` and a tier cell but no `api_opt_in`
- **THEN** `--check` SHALL report that cell `FAIL`, naming the target, the `api_opt_in` key and
  the routing file, and SHALL exit non-zero

#### Scenario: A named auth variable that is unset is caught

- **WHEN** a claude `api` cell declares `auth.env: ANTHROPIC_API_KEY` and the checking
  process's environment does not set it
- **THEN** `--check` SHALL report that cell unready and exit non-zero, rather than `ok`

#### Scenario: A codex api home check matches the spawn path

- **WHEN** a codex `api` cell declares an `auth.codex_home` whose directory has no `auth.json`
- **THEN** `--check` SHALL report that cell `FAIL` with the same remedy the spawn path raises,
  and SHALL exit non-zero

#### Scenario: A readiness failure is not a capacity gate

- **WHEN** a cell fails the readiness construction
- **THEN** the capacity cache SHALL contain no entry for that cell, so no later spawn can be
  silently routed past it

#### Scenario: A servable table still reports ok

- **WHEN** every declared cell's spawn construction succeeds
- **THEN** `--check` SHALL report every cell `ok` and exit zero
