# backlog-seeding Specification

## Purpose
Converts backlog invisible to unattended drains — specs awaiting a task DAG and epics with
unspecced features — into work-queue briefs mechanically, so planning work schedules itself
instead of waiting for a human to notice a dashboard stage.
## Requirements
### Requirement: needs-tasks specs are seeded as planning-only Route C briefs

The seeder SHALL capture one brief per spec reported in the `needs-tasks` dashboard stage, with
`recommended-route: C`, `implementation-intent: planning-only`, `target-spec` naming the spec id,
and a focus that directs the picking session to run spec-to-tasks against the existing spec
without re-authoring it. Specs in the `needs-clarification` stage SHALL NOT be seeded — their
next action requires human answers.

#### Scenario: A spec has an approved spec.md and no task DAG
- **WHEN** the seeder sweeps a repos root containing a spec whose dashboard stage is
  `needs-tasks`
- **THEN** a queued brief is created with `seeded-from: <repo>:spec:<spec-id>`,
  `recommended-route: C`, `implementation-intent: planning-only`, and `target-spec: <spec-id>`

#### Scenario: A spec has unresolved clarification markers
- **WHEN** the seeder sweeps a repos root containing a spec whose stage is `needs-clarification`
- **THEN** no brief is created for that spec

### Requirement: Epics with unspecced features are seeded by citation gap

For each epic file `docs/specs/epics/<NNN>-<slug>.md` whose `**Status:**` line is not terminal,
the seeder SHALL count `### Feature` headings and the spec folders whose top-level markdown
cites the epic. A spec/change folder counts as citing the epic when any of its top-level
markdown files matches at least one of: the epic's literal id string (e.g.
`002-safe-work-queue-dependency-references`); `Epic <NNN> Feature <M>` prose, where `<NNN>` is
the epic id's leading three-digit number and `<M>` is any digit sequence (case-insensitive); or
any of the epic's own documented `**Future spec id:**` values. When citations are fewer than
features, it SHALL capture one planning-only Route C brief whose focus directs the picking
session to spec the next unspecced feature — or, when the decomposition is in fact complete, to
flip the epic's `**Status:**` line to a terminal value so seeding stops. An epic with no
parseable `### Feature` headings SHALL be reported and never seeded (without a feature count
there is no terminal condition). Files in `epics/` that do not match the `NNN-slug` naming
pattern SHALL be ignored.

#### Scenario: An epic decomposes into more features than have citing specs
- **WHEN** an epic with a non-terminal status has 2 `### Feature` headings and 1 spec citing its
  id
- **THEN** a queued brief is created with `seeded-from: <repo>:epic:<epic-id>:cited=1`

#### Scenario: Every decomposed feature has a citing spec
- **WHEN** an epic's citing-spec count is greater than or equal to its feature count
- **THEN** no brief is created for that epic

#### Scenario: The epic's status line is terminal
- **WHEN** an epic's `**Status:**` line matches a terminal value (e.g. Completed, Superseded)
- **THEN** no brief is created regardless of its citation gap

#### Scenario: A change cites the epic by "Epic NNN Feature M" prose, not the literal id
- **WHEN** an epic `002-safe-work-queue-dependency-references` has 2 decomposed features, one
  spec cites its literal id, and a second change's `proposal.md` reads "Epic 002 Feature 2 adds
  this conservative runtime boundary" without ever containing the literal epic id string
- **THEN** both count as citing specs and no brief is created — the epic is fully cited

#### Scenario: A change cites the epic only by its documented future spec id
- **WHEN** an epic's Feature 1 section documents `**Future spec id:** `payments-core-ledger``
  and a change folder's markdown contains `payments-core-ledger` but never the epic's literal id
  string or "Epic NNN Feature M" prose
- **THEN** that change counts as a citing spec for the epic

#### Scenario: A bare mention of the epic number without "Feature" is not a citation
- **WHEN** a spec/change folder's markdown contains the epic's leading number in an unrelated
  context (e.g. "see PR 002" or "002 open items") but no literal epic id, no "Epic NNN Feature
  M" phrase, and no documented future spec id
- **THEN** that folder does not count as a citing spec

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

### Requirement: Seed keys are deduplicated against the whole queue and progress-keyed

The seeder SHALL never create a brief whose `seeded-from` key already exists in any brief under
queue/ or picked/, regardless of that brief's status. Spec seed keys SHALL be stable
(`<repo>:spec:<id>`) so a completed-but-fruitless brief terminates seeding for that spec; epic
seed keys SHALL embed the citation count (`<repo>:epic:<id>:cited=<n>`) so a newly citing spec
re-arms seeding for the next feature.

#### Scenario: A seeded brief was claimed and finished without clearing the stage
- **WHEN** the seeder re-sweeps a spec still in `needs-tasks` whose seed key exists on a done
  brief in picked/
- **THEN** no new brief is created for that spec

#### Scenario: A new spec cites the epic after the previous seeded brief finished
- **WHEN** the epic's citation count has increased since the last seeded brief
- **THEN** a new brief is created under the new `cited=<n>` key

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

### Requirement: Seeding is bounded, deterministic, and loudly capped

The seeder SHALL order candidates deterministically (needs-tasks specs, then epics, then
ready-to-implement specs, each sorted by repo then id), SHALL cap the briefs created per sweep
across all candidate kinds combined, and SHALL log the number of candidates deferred by the cap
rather than silently truncating.

#### Scenario: More fresh candidates than the per-sweep cap
- **WHEN** the seeder finds more unseeded candidates than the configured cap
- **THEN** it seeds the cap's worth in deterministic order and logs how many were deferred

### Requirement: The drain tops the queue up before draining, best-effort

`worktrail-drain` SHALL run the seeder after its pre-loop remediation sweep and before the first
ready-count check (so seeded briefs drain in the same pass), SHALL expose an opt-out flag, SHALL
skip seeding entirely in dry-run mode or when no repos root is configured, SHALL record the
seeder's summary under a `seeded_backlog` key in the run summary, and SHALL log-and-continue if
seeding raises rather than aborting the drain.

#### Scenario: A drain pass over a repos root with unseeded backlog
- **WHEN** `worktrail-drain` starts with a repos root containing a `needs-tasks` spec
- **THEN** the brief is seeded before the first iteration and the run summary's
  `seeded_backlog.seeded` names it

#### Scenario: Seeding fails
- **WHEN** the seeder raises during a drain pass
- **THEN** the drain logs a `seed-backlog error` line and continues to the queue loop

