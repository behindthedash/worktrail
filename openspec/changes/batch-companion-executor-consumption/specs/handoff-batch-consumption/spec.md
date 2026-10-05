# handoff-batch-consumption Specification

## Purpose

Make the front door's batch-consumption promise true on the execution side: a batch the claim
folded in (`claim-batch` stamps the primary's `batch:` list and each companion's
`batch-primary:`) reaches the executor as ONE union request -- the seed carries the claimed
companions, the handoff-seed flow folds them into one classification/run/worktree/PR, the run
record names every brief the run consumed, and each brief is closed or released individually
so batch execution never blurs per-brief completion state.

## ADDED Requirements

### Requirement: The seed carries the claimed batch's companions

`worktrail-handoff-seed seed PATH` SHALL read the brief's optional `batch:` frontmatter list
(the companion stems `claim-batch` recorded on the primary) and emit a `batch` list on the
seed output: one member per stem, in declared order, each member an object carrying `id` (the
stem), `path` (the resolved brief file beside the primary), and the same per-brief fields the
seed already maps for the primary -- `focus`, `repo`, `feature_idea`, `constraints` -- plus
`error`. A stem that does not resolve to a readable brief file beside the primary SHALL
produce a member whose `error` names the unresolved stem/path, without failing the seed, the
remaining members, or the top-level fields. A brief with no `batch:` list (or an empty one)
SHALL emit `"batch": []` and an otherwise unchanged seed. The mapper SHALL remain read-only:
it SHALL follow only the primary's own declared links and SHALL NOT list, move, stamp, or
claim any brief -- the queue lifecycle remains `work_queue.py`'s.

#### Scenario: Batch members ride the seed

- **WHEN** `seed` runs on a claimed primary whose frontmatter carries `batch: [comp-a, comp-b]`
  and both companion files sit beside it
- **THEN** the output's `batch` lists both members in that order, each with its own `focus`,
  `feature_idea`, `constraints`, `repo`, and a null `error`

#### Scenario: No batch means an unchanged single-brief seed

- **WHEN** `seed` runs on a brief with no `batch:` field
- **THEN** `batch` is an empty list and every other seed field is exactly what the single-brief
  mapping produced before

#### Scenario: A stem that no longer resolves is an error member, not a failure

- **WHEN** one stem's companion file is absent beside the primary (for example the companion
  was released back to `queue/`)
- **THEN** that member carries an `error` naming it, the other members and every top-level
  field are intact, and the command still exits zero

#### Scenario: The mapper resolves nothing it was not pointed at

- **WHEN** `seed` runs with companion files present
- **THEN** no brief file content changes and no file is moved or stamped by the mapper

### Requirement: The handoff-seed flow consumes the batch as one request

The executor's handoff-seed flow SHALL load every member of the seed's `batch` together with
the primary and SHALL treat them as ONE request: one classification (fed the primary's route
evidence), one run record, one worktree/PR. The request SHALL include each consumed companion's
focus, suggested approach, and constraints, labeled by its brief id, so the companion's scope
reaches the worker. A member the run cannot carry -- a member whose seed entry carries an
`error`, a member whose `repo` differs from the primary's resolved repo, or a member whose
route evidence (its `recommended_route`, when present) names a route other than the run's
resolved route -- SHALL be excluded from the request and released back to the queue with the
dispatch identity rather than forced in, and the exclusion SHALL be reported naming the brief
and the reason. A seed with an empty `batch` SHALL follow the unchanged single-brief path.

#### Scenario: The union reaches the worker as one request

- **WHEN** the seed carries two companions, both resolve, and both share the primary's repo
- **THEN** the run's request includes both companions' focus/suggested-approach/constraints
  labeled by brief id, one classification is made, and one run proceeds to one worktree/PR

#### Scenario: A companion that cannot ride is released, not forced in

- **WHEN** a member's `repo` differs from the primary's resolved repo
- **THEN** it is released back to the queue with the dispatch identity, the run is reported as
  proceeding without it, and it is not part of the request

#### Scenario: An unresolvable member is reported and left alone

- **WHEN** a member carries a seed `error`
- **THEN** the flow reports it by id, does not include it in the request, and does not mark it
  done or release it

#### Scenario: A single-brief handoff is unchanged

- **WHEN** the primary's seed has an empty `batch`
- **THEN** the flow behaves exactly as the single-brief handoff-seed flow did

### Requirement: The run record names every brief the run consumed

For a run seeded from handoff brief(s), the executor SHALL record every consumed brief id --
the primary and each companion folded into the union -- in the run record's
`handoffs_consumed` field as a YAML list, replacing any prior value
(`worktrail-run-record set-list`, never `set`), so the record names each brief the run
executed. A brief excluded from the union SHALL NOT be recorded as consumed. Recording SHALL
not alter any other run-record field.

#### Scenario: Every consumed brief id lands in the record

- **WHEN** a primary plus two folded companions complete a run
- **THEN** `handoffs_consumed` is a YAML list naming all three brief ids

#### Scenario: An excluded companion is not recorded as consumed

- **WHEN** a companion was released at fold time rather than executed
- **THEN** the recorded `handoffs_consumed` does not name it

### Requirement: Each consumed brief is closed individually

After the pipeline completes, the executor SHALL close each consumed brief individually --
`worktrail-work-queue done <id> --implementation-complete --run "$RUN"`, or `--planning-only`
when the run explicitly stopped at planning -- for every brief whose scope landed; no single
closure SHALL stand in for the batch. A consumed brief whose scope did NOT actually land SHALL
be released back to the queue instead of being marked done. Closures SHALL carry the dispatch
identity (`--by`) when the dispatch holds one.

#### Scenario: Each consumed brief is closed with the shared run

- **WHEN** a primary and two companions were consumed and all three scopes landed
- **THEN** three individual `done ... --run "$RUN"` closures happen, one per brief

#### Scenario: A companion whose scope did not land is released

- **WHEN** a consumed companion's scope did not actually land in the run's outcome
- **THEN** it is released back to the queue and is not marked done

#### Scenario: Single-brief closure is unchanged

- **WHEN** the handoff carried no companions
- **THEN** the brief is closed exactly as the single-brief flow closed it before

### Requirement: Operator references name the executor batch path

The batch-consumption reference SHALL name where the claimed batch is consumed -- the primary
brief's `batch:` frontmatter read back by `worktrail-handoff-seed` and folded by the executor's
`#handoff-seed` flow -- and where the run record's `handoffs_consumed` list is written; the
auto-mode reference's record line SHALL name the consumed set the same way, so a reader cannot
conflate the claimed set with the consumed one. Both references SHALL keep the existing promise
(one classification, one run record, one worktree/PR; per-brief `done`/`release`).

#### Scenario: The reference names the consumption path

- **WHEN** a reader follows the batch-consumption reference's union step
- **THEN** it names the `batch:` frontmatter, `worktrail-handoff-seed`, and the executor's
  `#handoff-seed` as where the batch is consumed

#### Scenario: Auto mode points at the consumed-set write

- **WHEN** the auto-mode reference describes the run record for a batched run
- **THEN** it says the record lists the briefs the run consumed and that the executor writes it
