**Status**: Completed

Add automated AC-verification to the worktrail SDD workflow's task-completion gate: before a task can be marked status:completed (or before its PR merges), re-run that task's own literal DoD assertions (grep checks, file-existence checks, referenced test commands) and fail if any are false.
