## ADDED Requirements

### Requirement: Status distinguishes an expired gate from an active one
`worktrail-agent-capacity status` SHALL label each gated entry (status `unavailable`, `gated`,
or `blocked`) according to its `retry_after`/`reset_at` window: `(active)` when the window has
not passed, `(expired)` when it has. An entry with a gated status and no timestamp SHALL print
with neither label. `status` SHALL remain read-only and SHALL NOT remove any entry.

#### Scenario: Expired drain gate is labelled
- **WHEN** the cache holds a bare `claude` entry with status `unavailable`, `source: drain`, and
  a `retry_after` earlier than now
- **THEN** `status` prints that key with `(expired)` and its `failure:` and `retry:` lines, and
  the cache file is unchanged afterwards

#### Scenario: Active gate keeps its label
- **WHEN** the cache holds `claude-sub:opus` with status `unavailable` and a `retry_after` later
  than now
- **THEN** `status` prints that key with `(active)` and no `(expired)` marker

### Requirement: Clear supports an expired-only scope
`worktrail-agent-capacity clear --expired --reason TEXT` SHALL remove exactly the entries that
`status` would label `(expired)`, append one `clear` audit entry with scope `expired` listing
the removed keys, and leave every other entry (active gates, timestamp-less gates, `available`
entries) untouched. It SHALL require a non-empty `--reason` like every other clear, SHALL exit 0
without modifying the file when nothing is expired, and SHALL reject being combined with a
provider key or `--all`.

#### Scenario: Only expired entries are removed
- **WHEN** the cache holds an expired bare `claude` gate, an active `codex-sub:gpt-5` gate, and
  an `available` `claude-api:opus` entry, and the operator runs
  `clear --expired --reason "stale drain gates"`
- **THEN** only `claude` is removed and printed as `cleared: claude`, the other two entries
  remain, and the audit log gains one entry with scope `expired` and providers `["claude"]`

#### Scenario: Nothing expired
- **WHEN** every gated entry's window is still in the future and the operator runs
  `clear --expired --reason "sweep"`
- **THEN** the command exits 0, prints nothing cleared, and the cache file is byte-identical
