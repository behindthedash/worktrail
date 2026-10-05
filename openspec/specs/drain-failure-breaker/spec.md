# drain-failure-breaker Specification

## Purpose

Define what advances `worktrail-drain`'s consecutive-failure circuit breaker, so an iteration
that is waiting for provider capacity is never counted -- or reported to the operator -- as a
failure. A capacity outage stops the drain through the capacity stop, which names the condition
that actually applies; the failure breaker stays reserved for iterations whose automation could
not finish the work.

## Requirements

### Requirement: A capacity-blocked iteration does not advance the failure breaker

The drain's consecutive-failure counter SHALL advance for failed iterations only. An iteration
whose outcome is a capacity block -- a record-less failure classified into the drain's capacity
failure classes (`auth`, `billing`), persisted as a capacity gate under the blocked cell's
target key -- SHALL NOT increment that counter. The capacity stop (`capacity_gated`, taken when
no configured candidate is selectable because every candidate's gate is active) SHALL remain the
sole stop for a capacity block, and the counter SHALL NOT be kept as a backstop for a gate the
drain's candidate selection cannot see.

Every other outcome SHALL keep its existing accounting: a `failed` iteration increments the
counter, a `success` resets it, `timeout_after_pr` and a `pending_user_decision` iteration leave
it unchanged, a `blocked_product_decision` iteration that filed at least one new open decision
does not count, a decision-less `blocked_product_decision` counts, and the remaining run-record
blocked states (`blocked_external_dependency`, `blocked_security_or_safety`) count. Only the
capacity block is exempt.

The drain's module docstring and `classify_outcome`'s docstring SHALL describe this accounting:
a capacity block does not count toward the circuit breaker and stops the run through the
capacity stop once the cache reflects it. A stop reason SHALL NOT describe a capacity-blocked
iteration as a failed one.

#### Scenario: Consecutive capacity blocks stop as a capacity outage

- **WHEN** consecutive iterations each end `blocked_capacity_billing`, each persisting its
  cell's capacity gate
- **THEN** the run's stop reason SHALL be the capacity stop, never the failure breaker, and no
  stop reason SHALL report those iterations as consecutive failed iterations

#### Scenario: The counter does not cut the failover chain short

- **WHEN** three candidates are configured and the first two iterations each end a capacity
  block, so a third candidate is still selectable
- **THEN** the drain SHALL attempt that third candidate, and SHALL stop only once every
  candidate's gate is active, as the capacity stop -- not as the failure breaker after the
  second block

#### Scenario: A plain failure still trips the breaker

- **WHEN** consecutive iterations complete as plain `failed` with no candidate capacity-gated
- **THEN** the circuit breaker SHALL stop the run at its configured threshold exactly as before

#### Scenario: A non-capacity blocked state still counts

- **WHEN** consecutive iterations end `blocked_external_dependency`
- **THEN** the circuit breaker SHALL stop the run at its configured threshold exactly as before,
  because that iteration is not waiting on provider capacity

#### Scenario: A decision-filed block is still exempt

- **WHEN** consecutive `blocked_product_decision` iterations each file a new open decision
- **THEN** the circuit breaker SHALL NOT trip, unchanged by this requirement
