## ADDED Requirements

### Requirement: A cooldown-derived gate is re-probed on a cadence
A capacity gate whose retry window was produced by a per-failure-class cooldown rather than by
the provider's own reset notice (`reset_source` absent or `cooldown`) SHALL NOT be treated as
authoritative for the whole window. Once the probe cadence (`GO_AGENT_GATE_PROBE_INTERVAL`
seconds, default 900) has elapsed since the entry's `checked_at` or its most recent `probe_at`,
`check()` SHALL let exactly one caller through per cadence, stamping `probe_at` on the entry
under the cache write lock so every other caller inside the cadence still sees the gate. The
caller's own spawn is the probe: a successful spawn records `available` (removing the gate) and
a failed spawn re-records the gate with a fresh window. An entry with `reset_source: provider`
SHALL never be probed, and an entry whose `failure_class` is `model_unavailable` SHALL never be
probed. `record()` SHALL accept `reset_source` and default it to `cooldown`; `spawn_agent`'s
exhausted-budget record SHALL pass `provider` together with the parsed timestamp when
`parse_explicit_reset()` finds one in the captured output, and SHALL otherwise fall back to the
class cooldown. `status` SHALL print a `probed:` line for an entry carrying `probe_at`.

#### Scenario: Stale billing gate lets one caller through after the cadence
- **WHEN** `claude-sub:fable` is gated `billing` with `retry_after` one hour out, `checked_at`
  twenty minutes ago, no `probe_at`, and the cadence is the default
- **THEN** the first `check("claude-sub", "fable")` returns without raising and the entry now
  carries `probe_at` equal to that call's `now`, and a second `check()` one second later raises
  `ProviderUnavailable`

#### Scenario: Gate inside the cadence is still authoritative
- **WHEN** the same gated entry has `checked_at` five minutes ago and no `probe_at`
- **THEN** `check()` raises `ProviderUnavailable` and the cache file is unchanged

#### Scenario: Provider-reported reset is never probed
- **WHEN** an entry is gated `billing` with `reset_source: provider`, `retry_after` three days out,
  and `checked_at` a day ago
- **THEN** `check()` raises `ProviderUnavailable` and no `probe_at` is written

#### Scenario: Retired model is never probed
- **WHEN** an entry is gated `model_unavailable` with `checked_at` two hours ago
- **THEN** `check()` raises `ProviderUnavailable` and no `probe_at` is written

#### Scenario: Successful probe clears the gate
- **WHEN** a probe-through spawn on the gated cell exits successfully
- **THEN** `spawn_agent` records `available` for that cell and a subsequent `check()` returns
  without raising

#### Scenario: Codex usage cap records the stated reset
- **WHEN** a codex cell exhausts its attempt budget with output containing "You've hit your usage
  limit ... try again at Aug 8th, 2026 2:17 AM."
- **THEN** the recorded gate's `retry_after` is that timestamp (UTC) and `reset_source` is
  `provider`

## MODIFIED Requirements

### Requirement: An auth failure gates its cell without retry
`agent_capacity.classify_failure` SHALL classify HTTP 401 / "unauthorized", a
consumed refresh token ("refresh token"), and "log out and sign in" wording as
`auth`. `agent_capacity` SHALL give `auth` a 24-hour default cooldown. When a
spawn's infra failure classifies as `auth`, `spawn_agent` SHALL record the gate
for the served cell on that first attempt and hop to the next cell in the row
with no further retry or backoff on the failed cell, logging the gated cell and
how to clear it. An `auth` gate is cooldown-derived and therefore probe-eligible:
once the probe cadence has elapsed, one spawn per cadence SHALL be let through,
and a successful one lifts the gate without operator action.

#### Scenario: Consumed refresh token gates on the first attempt
- **WHEN** a spawned codex cell exits non-zero with "Your access token could not
  be refreshed because your refresh token was already used"
- **THEN** the cell is recorded `unavailable` with `failure_class: auth` after one
  attempt, no backoff sleep occurs, and the spawn continues on the next cell in
  the row

#### Scenario: Auth gate outlives the run
- **WHEN** an `auth` gate is recorded
- **THEN** its `retry_after` is 24 hours out, so later spawns and later runs skip
  the cell until it expires, an operator clears it, or a probe-through spawn
  after the cadence succeeds

#### Scenario: Re-authenticated operator forgets to clear
- **WHEN** an `auth` gate was recorded more than one probe cadence ago and the operator has since
  re-authenticated
- **THEN** the next `check()` lets one spawn through, that spawn succeeds, and the gate is
  removed without `worktrail-agent-capacity clear`
