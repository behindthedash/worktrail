## ADDED Requirements

### Requirement: Explicit overrides retain the routing table that selected their target
When a live orchestrator spawn applies an explicit model or reasoning-effort
override, the temporary routing configuration SHALL reuse the target definition
from the resolved routing table that selected that target. The override path
SHALL NOT independently resolve repository or machine-wide policy to find that
target. If the selected-routing table does not declare the requested target,
the system SHALL fail with an actionable configuration error and SHALL NOT
launch a worker.

#### Scenario: Repository-local target survives an explicit model override
- **WHEN** a repository-local routing table selects a target that is absent from
  the machine-wide routing table and a live spawn has an explicit model override
- **THEN** the worker receives a temporary one-cell routing configuration for
  the repository-local target and dispatch proceeds using the override

#### Scenario: Explicit effort override preserves the selected target
- **WHEN** a live spawn selects a target and applies an explicit reasoning-effort
  override
- **THEN** the temporary one-cell routing configuration retains that target's
  declared harness and pool while applying the requested effort

#### Scenario: Selected-routing target is absent
- **WHEN** an explicit override requests a target absent from the routing table
  supplied for that spawn
- **THEN** dispatch fails before launching a worker and the configuration error
  identifies the missing target
