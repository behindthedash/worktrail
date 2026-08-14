## ADDED Requirements

### Requirement: Route D implementation seeding is opt-in per repo

The seeder SHALL only look for ready-to-implement specs, and SHALL only capture Route D
implementation briefs for them, in a repo whose policy sets `allow_seeded_implementation: true`.
The key SHALL default to `false`, so a repo that never sets it sees no change in seeding
behavior from this capability. This gate is independent of `max_seeds` and of the existing
`needs-tasks`/epic finders, which are unaffected by this key.

#### Scenario: A repo has not set allow_seeded_implementation
- **WHEN** the seeder sweeps a repo whose policy does not set `allow_seeded_implementation` (or
  sets it to a falsy value)
- **THEN** no Route D brief is created for that repo, even if it has specs in the
  `ready-to-implement` dashboard stage

#### Scenario: A repo sets allow_seeded_implementation: true
- **WHEN** the seeder sweeps a repo whose policy sets `allow_seeded_implementation: true`
- **THEN** that repo's specs in the `ready-to-implement` dashboard stage are eligible for Route D
  seeding

### Requirement: Ready-to-implement specs are seeded as Route D implementation briefs

For an opted-in repo, the seeder SHALL capture one brief per spec reported in the
`ready-to-implement` dashboard stage (task DAG complete, real pending implementation work
outstanding — not stale bookkeeping, not an already-stuck orchestrator run), with
`recommended-route: D`, `implementation-intent: requested`, and `target-spec` naming the spec id.
Specs in any other dashboard stage SHALL NOT be seeded by this finder.

#### Scenario: A spec's task DAG is complete with real pending implementation work
- **WHEN** the seeder sweeps an opted-in repo containing a spec whose dashboard stage is
  `ready-to-implement`
- **THEN** a queued brief is created with `seeded-from: <repo>:impl:<spec-id>`,
  `recommended-route: D`, `implementation-intent: requested`, and `target-spec: <spec-id>`

#### Scenario: A spec's remaining pending tasks are all already merged on the base branch
- **WHEN** the seeder sweeps an opted-in repo containing a spec whose dashboard stage is
  `stale-bookkeeping` (pending tasks stale, no real implementation work outstanding)
- **THEN** no Route D brief is created for that spec

#### Scenario: A prior orchestrator run on the spec is stuck
- **WHEN** the seeder sweeps an opted-in repo containing a spec whose dashboard stage is
  `orchestrator-stuck`
- **THEN** no Route D brief is created for that spec

### Requirement: Route D seed keys are deduplicated against the whole queue, never re-armed

The seeder SHALL never create a Route D brief whose `seeded-from` key already exists in any brief
under queue/ or picked/, regardless of that brief's status. Route D seed keys SHALL be stable
(`<repo>:impl:<spec-id>`) — unlike epic keys, they carry no progress-dependent suffix, so a
claimed-but-unfinished Route D brief permanently terminates seeding for that spec rather than
being re-created on the next sweep.

#### Scenario: A Route D brief was already seeded for a spec
- **WHEN** the seeder re-sweeps a spec still in `ready-to-implement` whose seed key
  `<repo>:impl:<spec-id>` already exists on a brief in queue/ or picked/, in any status
- **THEN** no new Route D brief is created for that spec

## MODIFIED Requirements

### Requirement: Seeding is bounded, deterministic, and loudly capped

The seeder SHALL order candidates deterministically (needs-tasks specs, then epics, then
ready-to-implement specs, each sorted by repo then id), SHALL cap the briefs created per sweep
across all candidate kinds combined, and SHALL log the number of candidates deferred by the cap
rather than silently truncating.

#### Scenario: More fresh candidates than the per-sweep cap
- **WHEN** the seeder finds more unseeded candidates than the configured cap
- **THEN** it seeds the cap's worth in deterministic order and logs how many were deferred
