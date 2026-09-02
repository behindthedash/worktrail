## ADDED Requirements

### Requirement: An auth failure gates its cell without retry
`agent_capacity.classify_failure` SHALL classify HTTP 401 / "unauthorized", a
consumed refresh token ("refresh token"), and "log out and sign in" wording as
`auth`. `agent_capacity` SHALL give `auth` a 24-hour default cooldown. When a
spawn's infra failure classifies as `auth`, `spawn_agent` SHALL record the gate
for the served cell on that first attempt and hop to the next cell in the row
with no further retry or backoff on the failed cell, logging the gated cell and
how to clear it.

#### Scenario: Consumed refresh token gates on the first attempt
- **WHEN** a spawned codex cell exits non-zero with "Your access token could not
  be refreshed because your refresh token was already used"
- **THEN** the cell is recorded `unavailable` with `failure_class: auth` after one
  attempt, no backoff sleep occurs, and the spawn continues on the next cell in
  the row

#### Scenario: Auth gate outlives the run
- **WHEN** an `auth` gate is recorded
- **THEN** its `retry_after` is 24 hours out, so later spawns and later runs skip
  the cell until it expires or an operator clears it
