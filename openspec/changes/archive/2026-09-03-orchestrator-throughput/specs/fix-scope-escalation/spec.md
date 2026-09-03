## Purpose

Turns an out-of-scope review finding into a bounded scope expansion instead of a quarantine: reviewer and fixer name the files they needed, the orchestrator widens the task's scope once and re-dispatches the fix without spending a strike, and the journal records what was added.

## ADDED Requirements

### Requirement: Workers report untouchable files as missing context paths
The reviewer and fixer prompts SHALL require that any file the worker needed to change but could not touch under its scope be listed in the report's `missing_context` as a repo-relative path, never only described in `notes`. A reviewer returning `review_status: FAILED` because the fix requires files outside the task's scope SHALL list those files; a fixer declining a finding as out of scope SHALL list the files it would have edited.

#### Scenario: Reviewer names the asserting test file
- **WHEN** the reviewer fails a task because `tests/workqueue/test_queue_triage.py` asserts the old behavior and is outside scope
- **THEN** the review report's `missing_context` contains `tests/workqueue/test_queue_triage.py`

#### Scenario: Prompt text carries the rule
- **WHEN** a reviewer or fixer prompt is built
- **THEN** it instructs the worker to list untouchable files in `missing_context` as repo-relative paths

### Requirement: A failed review with missing-context paths triggers scope escalation
The orchestrator SHALL treat a review report with `review_status: FAILED` whose `missing_context` lists at least one existing repo-relative file outside the task's scope as a scope-escalation trigger, and SHALL treat a fix report listing such paths the same way regardless of the fix report's `status`. Escalation SHALL fire at most once per task and SHALL NOT fire when any listed path is declared by another in-flight task.

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

### Requirement: Escalation widens scope and re-dispatches the fix without a strike
When escalation fires, the orchestrator SHALL add the paths to the task's `files` for the remaining strikes, SHALL pass them as extra reads to the next fix dispatch, and SHALL dispatch that fix without counting the triggering review against the task's strike budget.

#### Scenario: Triggering review does not consume a strike
- **WHEN** a task's second review fails with escalating paths under a three-strike budget
- **THEN** the task's retry count after escalation equals its count before that review

### Requirement: A task with a pending escalation is never quarantined
The orchestrator SHALL NOT move a task to `escalated` or `failed` while a scope escalation has been granted and the fix that consumes it has not yet been dispatched.

#### Scenario: Third-strike review that escalates is not terminal
- **WHEN** a review fails on what would otherwise be the final strike and its report lists escalating paths
- **THEN** the task returns to the fix state instead of `escalated`

### Requirement: Escalated files are journaled
The journal entry for the report that triggered escalation SHALL carry `scope_escalated: true` and `scope_escalated_files` listing the added repo-relative paths, and replaying the journal SHALL restore the widened scope and the fix state.

#### Scenario: Resume restores widened scope
- **WHEN** a run resumes from a journal whose review entry carries `scope_escalated_files: ["tests/workqueue/test_queue_triage.py"]`
- **THEN** the task's `files` includes that path and its status is the fix state
