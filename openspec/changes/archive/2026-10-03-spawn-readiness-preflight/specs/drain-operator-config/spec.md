## ADDED Requirements

### Requirement: A spawn-readiness failure stops a drain before any spawn

Before running any pre-pass that spawns an agent and before its first iteration,
`worktrail-drain` SHALL prove that every tier cell its routing table could serve can be
constructed — by resolving the table through the same `load_policy()` → `resolve_routing()` →
`build_child_env()` path a spawn uses, against the drain process's own environment — and SHALL
resolve the table itself for that purpose rather than reusing a routing mapping a caller
happens to have in scope.

A cell that cannot be constructed SHALL stop the run with exit 2 before any worker is launched,
naming the offending cell, the reason and the routing file; the run SHALL NOT start, and SHALL
NOT fall through to the next rung. This covers an unresolvable environment profile, a claude
`api` target with no usable auth source or an unset `auth.env` variable, a codex `api` home
that is undeclared or unprovisioned, an `api`-pool target declared without `api_opt_in`, and a
harness outside the supported set.

A cell that is only capacity-gated SHALL NOT stop the run: the gate is recorded and selection
walks past it, exactly as before this change.

#### Scenario: An unready table stops the run before the intake-triage pre-pass

- **WHEN** `--intake-triage` is set and a declared cell's spawn construction fails
- **THEN** the drain SHALL exit 2 naming that cell and the routing file, and SHALL NOT spawn an
  intake-triage evaluator

#### Scenario: A capacity-gated cell does not stop the run

- **WHEN** a `default_tier` cell names an opencode model absent from `opencode models` and every
  other declared cell constructs
- **THEN** the drain SHALL proceed, with that cell gated and the next ungated cell selected for
  the first iteration

#### Scenario: A ready table starts normally

- **WHEN** every declared cell's spawn construction succeeds
- **THEN** the drain SHALL proceed to its pre-passes and its first iteration unchanged
