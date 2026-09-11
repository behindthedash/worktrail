## ADDED Requirements

### Requirement: A failed report naming a merged sibling file triggers auto-recovery

When a worker report would drive a task to a terminal `failed` or `escalated`
status, the orchestrator SHALL inspect the report's `missing_context` and SHALL
treat the task as auto-recoverable when at least one listed path is a valid
repo-relative path that is declared in the `files` of another task in the same
RunPlan, is absent from the task's worktree, and exists with a non-empty blob
on the live base ref. Recovery SHALL fire at most once per task per run. A
path that exists in the task worktree, is not declared by any other task, is
absent from the base ref, or is an empty blob on the base ref SHALL NOT
qualify, and when the base ref cannot be resolved recovery SHALL NOT fire.

#### Scenario: Sibling file merged to base after the fork

- **WHEN** task 4.1's implement report has `status: failed` and
  `missing_context: ["bin/sync-doc.py"]`, task 3.1 declares `bin/sync-doc.py`,
  the file is not in 4.1's worktree, and `origin/main` contains it with content
- **THEN** task 4.1 is auto-recovered instead of transitioning to `failed`

#### Scenario: Path present in the worktree is left to scope escalation

- **WHEN** the listed path exists as a file in the task's worktree
- **THEN** auto-recovery does not fire and the existing scope-escalation rule
  decides the report

#### Scenario: Path not declared by a sibling task

- **WHEN** the listed path is absent from the worktree and present on base but
  no other task in the RunPlan declares it
- **THEN** auto-recovery does not fire and the report is applied as an
  ordinary failure

#### Scenario: Sibling not yet on base

- **WHEN** the listed path is declared by a sibling task but does not exist on
  the live base ref
- **THEN** auto-recovery does not fire and the report is applied as an
  ordinary failure

#### Scenario: Second qualifying report on the same task

- **WHEN** a task that was already auto-recovered in this run produces another
  report listing a qualifying path
- **THEN** the report is applied as an ordinary failure and the task reaches a
  terminal status

### Requirement: Recovery re-dispatches from a fresh worktree without a human

When auto-recovery fires, the orchestrator SHALL remove the task's worktree and
branch, SHALL reset the task to `pending` with its strike count cleared and any
pending scope-escalation or extra-read state dropped, and SHALL leave the task
to the frontier scheduler so it is re-dispatched from a fresh stacked worktree
forked from the current base. The group containing the task SHALL NOT be
quarantined on account of the recovered report.

#### Scenario: Task is re-dispatched in the same run

- **WHEN** auto-recovery fires for a task while the run has dispatch capacity
- **THEN** the task is dispatched again with a newly created worktree whose
  tree contains the sibling's file, and the group proceeds to integration
  when the task completes

#### Scenario: Stale worktree and branch are gone

- **WHEN** auto-recovery fires
- **THEN** the task's previous worktree path no longer exists and its previous
  branch is deleted before the re-dispatch creates a new one

#### Scenario: Group is not quarantined by the recovered report

- **WHEN** the recovered task's group would otherwise have been quarantined
  because that task was terminal `failed`
- **THEN** no quarantine record is written for the group on that basis

### Requirement: Recovery is journaled as an auditable intervention

The orchestrator SHALL append a journal event entry
`missing_context_auto_recovery` carrying the task id, the qualifying paths,
the sibling task ids that declare them, the resolved base ref and its commit,
the triggering role, and `category: orchestrator_defect`, and SHALL persist it
before the task is re-dispatched. The triggering report's own journal entry
SHALL be recorded with `auto_recovered: true` and SHALL NOT carry a terminal
`terminal_status`. Replaying the journal on resume SHALL restore the task to
`pending` with its once-only recovery guard set, and `clear_tasks()` SHALL
have nothing terminal to remove for a recovered task.

#### Scenario: Event entry is written

- **WHEN** auto-recovery fires for task 4.1 because of `bin/sync-doc.py`
  declared by task 3.1
- **THEN** the journal contains an event entry naming task 4.1, the path, task
  3.1, the base ref and commit, and `category: orchestrator_defect`

#### Scenario: Resume after recovery

- **WHEN** a run is resumed after the recovery event was journaled but before
  the re-dispatched task completed
- **THEN** replay leaves the task `pending` with the recovery guard set and
  does not treat the triggering report as terminal

#### Scenario: Operator log line

- **WHEN** auto-recovery fires
- **THEN** the run output prints one line naming the task, the recovered
  paths, and the sibling task, so an operator watching the run sees the
  intervention without reading the journal
