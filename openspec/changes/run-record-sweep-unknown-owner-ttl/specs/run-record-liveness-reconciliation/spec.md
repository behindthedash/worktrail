## MODIFIED Requirements

### Requirement: Orphan sweeping requires confirmed dead-owner evidence

`sweep-orphans` SHALL terminalize only non-terminal records classified as
confirmed orphans, plus -- only when the caller passes
`--unknown-owner-ttl-seconds N` -- non-terminal records classified as unknown
owner whose heartbeat age is parsable and greater than `N` seconds and which
carry no `worktree`, an empty `files_changed`, and no `pull_request`. It SHALL
not terminalize a record whose detached owner is running, whose heartbeat is
fresh after owner exit, or whose owner evidence is unavailable or unknown while
the unknown-owner TTL is unset, not yet exceeded, or not comparable because the
record has no parsable heartbeat. It SHALL not terminalize an unknown-owner
record that has a worktree, changed files, or a pull request regardless of
heartbeat age. A record closed under the unknown-owner TTL SHALL be finished
with the requested completion status and an auto-reconciliation note naming
`reconciliation=unknown_owner`, the heartbeat age, and the TTL that expired.
Its JSON summary SHALL separately report records skipped for an active
detached process and an unknown owner so an operator can reconcile them without
treating the absence of a heartbeat as a verdict.

#### Scenario: A stale heartbeat belongs to a running detached process

- **WHEN** `sweep-orphans` encounters a non-terminal record with a stale
  heartbeat and a bound owner reporting `running`
- **THEN** it SHALL leave the record non-terminal and list it as skipped for
  an active process

#### Scenario: A confirmed orphan is swept

- **WHEN** `sweep-orphans` encounters a non-terminal record classified as a
  confirmed orphan
- **THEN** it SHALL finish the record with the requested completion status and
  record an auto-reconciliation note that identifies the liveness evidence

#### Scenario: Unknown-owner record past the TTL with no work product is swept

- **WHEN** `sweep-orphans` is run with `--unknown-owner-ttl-seconds 86400` and
  encounters a non-terminal unbound record whose heartbeat is older than 86400
  seconds, with `worktree` null, `files_changed` empty, and no `pull_request`
- **THEN** it SHALL finish the record with the requested completion status,
  list it under `closed`, and record a note naming `reconciliation=unknown_owner`
  and the expired TTL

#### Scenario: Unknown-owner record younger than the TTL is retained

- **WHEN** `sweep-orphans` is run with `--unknown-owner-ttl-seconds 86400` and
  encounters a non-terminal unbound record whose heartbeat is stale but younger
  than 86400 seconds
- **THEN** it SHALL leave the record non-terminal and list it under
  `skipped_unknown_owner`

#### Scenario: Unknown-owner record with a work product is retained past the TTL

- **WHEN** `sweep-orphans` is run with `--unknown-owner-ttl-seconds 86400` and
  encounters a non-terminal unbound record older than the TTL that has a
  `worktree`, a non-empty `files_changed`, or a `pull_request`
- **THEN** it SHALL leave the record non-terminal and list it under
  `skipped_unknown_owner`

#### Scenario: Unknown-owner record with no parsable heartbeat is retained

- **WHEN** `sweep-orphans` is run with `--unknown-owner-ttl-seconds 86400` and
  encounters a non-terminal unbound record with no `updated_at` or an
  unparsable one
- **THEN** it SHALL leave the record non-terminal and list it under
  `skipped_unknown_owner`

#### Scenario: Unknown-owner TTL is off by default

- **WHEN** `sweep-orphans` is run without `--unknown-owner-ttl-seconds` and
  encounters a non-terminal unbound record with a heartbeat older than any
  plausible TTL and no work product
- **THEN** it SHALL leave the record non-terminal and list it under
  `skipped_unknown_owner`
