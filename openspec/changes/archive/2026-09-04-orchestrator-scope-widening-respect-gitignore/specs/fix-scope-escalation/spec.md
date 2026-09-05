## MODIFIED Requirements

### Requirement: A failed review with missing-context paths triggers scope escalation
The orchestrator SHALL treat a review report with `review_status: FAILED` whose `missing_context` lists at least one existing repo-relative file outside the task's scope as a scope-escalation trigger, and SHALL treat a fix report listing such paths the same way regardless of the fix report's `status`. Escalation SHALL fire at most once per task and SHALL NOT fire when any listed path is declared by another in-flight task. A listed path that `git check-ignore` reports as ignored in the task's worktree SHALL NOT count as a valid escalation candidate; if every existing, non-colliding listed path is gitignored, escalation SHALL NOT fire.

#### Scenario: Reviewer-triggered escalation
- **WHEN** the first review of a task fails with `missing_context: ["tests/workqueue/test_queue_triage.py"]` and no in-flight task declares that file
- **THEN** the task's `files` gains that path and the task returns to the fix state

#### Scenario: Fixer-triggered escalation on a successful fix report
- **WHEN** a fix report has `status: success` and `missing_context: ["tests/router/test_skill_dispatch.py"]`
- **THEN** the task's `files` gains that path and a further fix is dispatched with the widened scope

#### Scenario: Collision with an in-flight task blocks escalation
- **WHEN** the listed path is declared by another task currently in flight
- **THEN** the task's scope is unchanged and the report is applied as an ordinary review or fix result

#### Scenario: Escalation fires only once
- **WHEN** a task that already escalated once receives another report listing a new path
- **THEN** the task's scope is unchanged

#### Scenario: A gitignored path is not granted as scope
- **WHEN** a review report lists `missing_context: [".claude/tsc-cache/abc123/affected-repos.txt"]`, the path exists in the worktree, and `.gitignore` matches it
- **THEN** the task's scope is unchanged and escalation does not fire

#### Scenario: A gitignored path does not block escalation on a real path in the same report
- **WHEN** a review report lists `missing_context: [".claude/tsc-cache/abc123/edited-files.log", "src/foo.py"]`, both exist in the worktree, and only the `.claude/tsc-cache/` path is gitignored
- **THEN** the task's `files` gains `src/foo.py` only and the task returns to the fix state
