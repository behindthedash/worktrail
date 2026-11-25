## MODIFIED Requirements

### Requirement: Serial plans are rejected at compile
The compile step SHALL compute the critical path and width of the merged fan-out tasks,
excluding tail kinds (`e2e`, `cleanup`) and `docs`-kind tasks, and SHALL reject the plan when
the critical path exceeds `max(width, compile_max_critical_path_over_width)`. Dependency edges
through an excluded task SHALL NOT be used to connect evaluated tasks into a chain. The
rejection line SHALL name the task ids on the longest chain and instruct the author to
consolidate them or declare disjoint file scope so they can run in parallel.

#### Scenario: The evidence run's shape is rejected
- **WHEN** a `tasks.md` compiles to 18 fan-out tasks with critical path 9 and width 5 under the default threshold 2
- **THEN** compile exits 1 with a problem line naming the chain's task ids and no run-plan marker is written

#### Scenario: A wide plan passes
- **WHEN** a `tasks.md` compiles to 7 independent fan-out tasks and one `[e2e]` tail
- **THEN** compile reports critical path 1, emits no plan-shape problem, and exits 0

#### Scenario: A pending docs chain does not count toward the serial rule
- **WHEN** a completed implementation task is followed by three pending `docs` tasks, each
  depending on the previous and each declaring the change's own
  `openspec/changes/<id>/tasks.md`
- **THEN** compile emits no serial-rule problem and exits 0

#### Scenario: Excluding docs does not hide a serial implementation chain
- **WHEN** a plan has five dependent implementation tasks declaring distinct files alongside
  unrelated `docs` tasks
- **THEN** compile still rejects it with the serial-rule problem naming the implementation
  chain

#### Scenario: A docs-only pending plan is still rejected
- **WHEN** every task in the plan is `docs`-kind
- **THEN** compile is rejected by the no-fan-out rule rather than accepted by the serial
  rule's exemption

### Requirement: Same-file dependent chains are rejected at compile
The compile step SHALL reject a plan containing more than `compile_max_same_file_chain`
consecutive dependent implementation tasks whose declared `files:` is the same single file,
excluding `docs`-kind tasks and tail kinds from both the chain and the file's writer set. The
rejection line SHALL name the chained task ids and the file and instruct the author to fold
them into one task with sub-bullets.

#### Scenario: The evidence run's group 4 is rejected
- **WHEN** tasks 4.1, 4.2, and 4.4 each declare only `src/worktrail/workqueue/queue_triage.py` and each depends on the previous one, under the default threshold 2
- **THEN** compile exits 1 with a problem line naming `4.1, 4.2, 4.4` and that file

#### Scenario: A consolidated group passes
- **WHEN** the same work is authored as one task declaring `src/worktrail/workqueue/queue_triage.py` and its test files
- **THEN** compile emits no same-file-chain problem for that group

#### Scenario: Docs tasks sharing the change's task list are not a same-file chain
- **WHEN** three pending `docs` tasks each declare only `openspec/changes/<id>/tasks.md` and
  each depends on the previous one, alongside a completed implementation task
- **THEN** compile emits no same-file-chain problem for `tasks.md` and exits 0

#### Scenario: An implementation same-file chain with docs present is still rejected
- **WHEN** three dependent implementation tasks each declare only
  `src/worktrail/conductor/parallelism.py` and unrelated `docs` tasks are also present
- **THEN** compile exits 1 with a same-file-chain problem naming the three implementation
  tasks and that file
