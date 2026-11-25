## MODIFIED Requirements

### Requirement: needs-tasks specs are seeded as planning-only Route C briefs

The seeder SHALL capture one brief per spec reported in the `needs-tasks` dashboard stage, with
`recommended-route: C`, `implementation-intent: planning-only`, `target-spec` naming the spec id,
and a focus that directs the picking session to run spec-to-tasks against the existing spec
without re-authoring it. Specs in the `needs-clarification` stage SHALL NOT be seeded — their
next action requires human answers. An OpenSpec change whose `tasks.md` carries at least one
untrackable top-level checkbox SHALL NOT be seeded with the spec-to-tasks brief; it is seeded as
a task-id normalization brief under `Requirement: Untrackable OpenSpec task ids are seeded as a
normalization brief` instead.

#### Scenario: A spec has an approved spec.md and no task DAG

- **WHEN** the seeder sweeps a repos root containing a spec whose dashboard stage is
  `needs-tasks`
- **THEN** a queued brief is created with `seeded-from: <repo>:spec:<spec-id>`,
  `recommended-route: C`, `implementation-intent: planning-only`, and `target-spec: <spec-id>`

#### Scenario: A spec has unresolved clarification markers

- **WHEN** the seeder sweeps a repos root containing a spec whose stage is
  `needs-clarification`
- **THEN** no brief is created for that spec

#### Scenario: An OpenSpec change has no task checkboxes at all

- **WHEN** an OpenSpec change in the `needs-tasks` stage has an empty `tasks.md` with no
  checkboxes
- **THEN** the ordinary spec-to-tasks Route C brief is created, because no task list exists to
  normalize

### Requirement: Seeding is bounded, deterministic, and loudly capped

The seeder SHALL order candidates deterministically (task-id normalization candidates first,
then ordinary needs-tasks specs, then epics, then ready-to-implement specs, each group sorted by
repo then id), SHALL cap the briefs created per sweep across all candidate kinds combined, and
SHALL log the number of candidates deferred by the cap rather than silently truncating.

#### Scenario: More fresh candidates than the per-sweep cap

- **WHEN** the seeder finds more unseeded candidates than the configured cap
- **THEN** it seeds the cap's worth in deterministic order and logs how many were deferred

#### Scenario: Normalization and ordinary needs-tasks candidates compete for one slot

- **WHEN** a sweep finds one task-id normalization candidate and one ordinary needs-tasks spec
  with a cap of one
- **THEN** the normalization candidate is seeded and the ordinary spec is reported as deferred

## ADDED Requirements

### Requirement: Untrackable OpenSpec task ids are seeded as a normalization brief

When an OpenSpec change's `tasks.md` contains at least one untrackable top-level checkbox, the
seeder SHALL capture one planning-only Route C brief whose focus directs the picking session to
normalize the task ids in place — through `worktrail-normalize-openspec-task-ids` or the
equivalent deterministic `N.M` assignment — and SHALL NOT direct it to run spec-to-tasks or
re-author the task list. The brief SHALL use the stable seed key
`<repo>:normalize:<spec-id>`, distinct from the `<repo>:spec:<spec-id>` key, and
`target-spec: <spec-id>`.

#### Scenario: An OpenSpec change has id-less checkboxes

- **WHEN** the seeder sweeps a repo containing an OpenSpec change whose `tasks.md` checkboxes
  have no `N.M` ids
- **THEN** a queued brief is created with `seeded-from: <repo>:normalize:<spec-id>`,
  `recommended-route: C`, `implementation-intent: planning-only`, and a focus naming task-id
  normalization rather than spec-to-tasks

#### Scenario: A normalization brief was already seeded

- **WHEN** the seeder re-sweeps the same change and the `<repo>:normalize:<spec-id>` key
  already exists on a brief in queue/ or picked/, in any status
- **THEN** no new normalization brief is created for that change

#### Scenario: An earlier task-creation brief does not suppress the normalization brief

- **WHEN** a `<repo>:spec:<spec-id>` brief already exists for a change that has id-less
  checkboxes
- **THEN** the normalization brief is still seeded, because it uses the distinct
  `<repo>:normalize:<spec-id>` key
