## ADDED Requirements

### Requirement: Drain prunes its own expired gates when it records a new one
When `worktrail-drain` persists a capacity gate, it SHALL, within the same locked
read-modify-write, remove every existing entry whose `source` is `drain` and whose
`retry_after`/`reset_at` has already passed. Entries with any other `source`, entries with no
timestamp, and drain entries whose window is still active SHALL be left untouched. This is the
only place the drain removes cache entries; its read paths remain read-only.

#### Scenario: Stale drain gate for another agent is dropped
- **WHEN** the cache holds a bare `codex` entry with `source: drain` and a `retry_after` earlier
  than now, and the drain records a new `claude` gate
- **THEN** the written cache contains the new `claude` gate and no `codex` entry

#### Scenario: Spawn-sourced and active entries survive
- **WHEN** the cache holds an expired `claude-sub:opus` entry with `source: spawn` and an
  active `codex` entry with `source: drain`, and the drain records a new `claude` gate
- **THEN** both pre-existing entries are still present in the written cache alongside the new
  gate
