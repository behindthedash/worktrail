## Purpose

Makes `worktrail-compile` reject a plan whose shape guarantees a slow or self-quarantining run (a serial chain, a same-file dependent chain, or an implementation task with no test scope) before any worktree is created, instead of printing an advisory warning that nothing acts on.

## ADDED Requirements

### Requirement: Serial plans are rejected at compile
The compile step SHALL compute the critical path and width of the merged fan-out tasks (tail kinds excluded) and SHALL reject the plan when the critical path exceeds `max(width, compile_max_critical_path_over_width)`. The rejection line SHALL name the task ids on the longest chain and instruct the author to consolidate them or declare disjoint file scope so they can run in parallel.

#### Scenario: The evidence run's shape is rejected
- **WHEN** a `tasks.md` compiles to 18 fan-out tasks with critical path 9 and width 5 under the default threshold 2
- **THEN** compile exits 1 with a problem line naming the chain's task ids and no run-plan marker is written

#### Scenario: A wide plan passes
- **WHEN** a `tasks.md` compiles to 7 independent fan-out tasks and one `[e2e]` tail
- **THEN** compile reports critical path 1, emits no plan-shape problem, and exits 0

### Requirement: Same-file dependent chains are rejected at compile
The compile step SHALL reject a plan containing more than `compile_max_same_file_chain` consecutive dependent tasks whose declared `files:` is the same single file. The rejection line SHALL name the chained task ids and the file and instruct the author to fold them into one task with sub-bullets.

#### Scenario: The evidence run's group 4 is rejected
- **WHEN** tasks 4.1, 4.2, and 4.4 each declare only `src/worktrail/workqueue/queue_triage.py` and each depends on the previous one, under the default threshold 2
- **THEN** compile exits 1 with a problem line naming `4.1, 4.2, 4.4` and that file

#### Scenario: A consolidated group passes
- **WHEN** the same work is authored as one task declaring `src/worktrail/workqueue/queue_triage.py` and its test files
- **THEN** compile emits no same-file-chain problem for that group

### Requirement: Implementation tasks without test scope are rejected when a test counterpart exists
The compile step SHALL reject an implementation task (kind not `e2e`, `cleanup`, or `docs`) whose `files:` names a path under `src/` but no path under `tests/` when the repository already contains a test file whose name matches that module's stem under `tests/`. The rejection line SHALL name the task id and the existing test file to add to its scope. A task whose source module has no existing test counterpart SHALL NOT be rejected by this rule.

#### Scenario: Implementation task omits its asserting test file
- **WHEN** task 1.2 declares only `src/worktrail/workqueue/create_handoff.py` and `tests/workqueue/test_create_handoff.py` exists in the repository
- **THEN** compile exits 1 with a problem line naming `1.2` and `tests/workqueue/test_create_handoff.py`

#### Scenario: New module with no test counterpart passes
- **WHEN** task 1.1 declares only `src/worktrail/workqueue/repo_inference.py` and no `tests/**/test_repo_inference*.py` exists
- **THEN** compile emits no missing-test-scope problem for `1.1`

### Requirement: Plan-shape rejections propagate to every compile consumer
Plan-shape problems SHALL be reported on stderr and SHALL make `worktrail-compile` exit 1 in both its text and `--json` output modes, SHALL prevent the compile marker from being written, and SHALL stop the orchestrator's pre-fan-out plan application before any task worktree is created. The existing advisory serial warning is replaced by the rejection, not printed alongside it.

#### Scenario: Orchestrator refuses to fan out a rejected plan
- **WHEN** the orchestrator applies a run plan whose merged tasks violate a plan-shape rule
- **THEN** it raises with the same problem lines and creates no task worktree

#### Scenario: Pipeline scope-check step stops on rejection
- **WHEN** the worktrail-go pipeline's scope-check step runs `worktrail-compile` against a change that violates a plan-shape rule
- **THEN** the step's existing non-zero-exit handling stops the pipeline with no change to the skill prose

### Requirement: Plan-shape thresholds are policy keys
`compile_max_critical_path_over_width` and `compile_max_same_file_chain` SHALL be repo policy keys defaulting to 2, and each SHALL be forced back to its default with a warning when it is not a non-boolean integer of at least 1.

#### Scenario: Invalid threshold falls back
- **WHEN** a policy file sets `compile_max_same_file_chain: yes`
- **THEN** loading the policy yields 2 for that key and records a warning naming it

#### Scenario: Valid threshold is honored
- **WHEN** a policy file sets `compile_max_critical_path_over_width: 4`
- **THEN** a plan with critical path 4 and width 2 is not rejected by the serial-plan rule
