# task-authoring-co-scoping-guidance Specification

## Purpose
Governs how the bundled `openspec-propose` skill's tasks-artifact step sizes implementation tasks, co-scopes each task with the tests that assert the behavior it changes, and marks mechanical tasks as review-exempt, so a compiled plan is wide, self-contained per task, and cheap to review.
## Requirements
### Requirement: Implementation tasks are coarse and module-scoped
The tasks-artifact authoring guidance SHALL instruct the author to produce one implementation task per module per phase, sized for roughly 20 to 60 minutes of implementation, and to fold consecutive steps that edit the same file into one task with sub-bullets rather than a dependent chain. The guidance SHALL compose with the existing per-phase hot-file ownership guidance by cross-referencing it, not restating it.

#### Scenario: Consecutive same-file steps become one task
- **WHEN** the author would otherwise write tasks 4.1, 4.2, and 4.4 that each declare `files: src/worktrail/workqueue/queue_triage.py`
- **THEN** the guidance directs a single task 4.1 whose body lists those steps as sub-bullets and whose `files:` names that module once

#### Scenario: Guidance text is present in the bundled skill
- **WHEN** the plugin surface test reads `skills/openspec-propose/SKILL.md`
- **THEN** it finds the one-task-per-module sizing rule and the same-file-chain folding rule in the tasks-artifact step

### Requirement: Implementation tasks co-scope the tests they change
The guidance SHALL require that an implementation task's `files:` declaration include every existing test file that asserts behavior the task changes and the new test file the task adds, and SHALL forbid splitting an implementation and its tests into separate tasks.

#### Scenario: Behavior change names its asserting test
- **WHEN** a task replaces the repo-prefix inference in `create_handoff.py` whose old behavior `tests/workqueue/test_create_handoff.py` asserts
- **THEN** the task's `files:` names both `src/worktrail/workqueue/create_handoff.py` and `tests/workqueue/test_create_handoff.py`

#### Scenario: Co-scoping rule is present in the bundled skill
- **WHEN** the plugin surface test reads `skills/openspec-propose/SKILL.md`
- **THEN** it finds the rule that implementation and its tests are never split into separate tasks

### Requirement: Mechanical tasks opt out of review at authoring time
The guidance SHALL instruct the author to mark a task whose diff is mechanical or docs-only (configuration keys, prose, a single constant) with an indented `review: skip` continuation line so the orchestrator's review-exempt fast path applies, and SHALL state that a task producing executable behavior never carries it.

#### Scenario: Config-only task is marked review-exempt
- **WHEN** a task only edits `.worktrail/policy.yaml`
- **THEN** the guidance directs a `review: skip` line under that task

#### Scenario: Review-skip rule is present in the bundled skill
- **WHEN** the plugin surface test reads `skills/openspec-propose/SKILL.md`
- **THEN** it finds the `review: skip` rule in the tasks-artifact step

