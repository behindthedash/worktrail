## Context

The pipeline scheduler treats "every group terminal" as "safe to run the tail". Terminal
includes `OPEN` (PR not merged yet) and `QUARANTINED` (integration failed), so a tail task's
dependencies may be absent from `<remote>/<base>` when the tail pass starts. Tail tasks are
exactly the case that assumes squash-merged dependencies (see `_dispatch_pending_tail`'s
docstring), so the assumption must be checked rather than implied.

## Goals / Non-Goals

- Goals: never dispatch a tail task whose declared dependencies have not merged; keep the
  held task resumable with zero manual repair; make the hold visible in the journal and log.
- Non-Goals: changing `TERMINAL_GROUP_STATES` or `integrate_complete`; waiting/polling for an
  OPEN PR to merge inside the run (resume already covers that); changing how tail tasks are
  reconciled after they run.

## Decisions

- **Hold, not skip or fail.** Three options were on the table: (a) hold the task `pending`
  and re-check on resume; (b) skip it and mark it done; (c) fail/escalate it. (b) lies about
  verification and (c) creates human work for a state that resolves on its own once the PR
  merges. (a) is level-triggered and matches how resume already treats everything else.
- **Gate on the group, not the task status.** A dependency task can be `done` in `tasks.md`
  (the worker ticked it) while its group PR is OPEN or quarantined. Group journal state is the
  only signal that the work is on base, so the gate reads `journal["groups"][name]["state"]`
  for the group that contains each non-tail dependency. Dependencies that are themselves
  tail-kind are not gated by this check (they are ordered by the frontier as before).
- **Direct dependencies only.** Transitive merge state is already implied: a group is only
  `MERGED` after its base group merged (base-before-dependent ordering in the scheduler).
- **Exclusion seam instead of `only`.** `only` pre-marks everything outside the list as
  completed, which would unblock a held task's dependents. `live_run_real` instead accepts an
  `exclude` list that drops those tasks from the in-memory list before scheduling, leaving
  their status on disk untouched.
- **Pure helper in `coordinator.py`.** `tail_blocked_task_ids(tasks, groups, journal_groups)
  -> dict[str, list[str]]` (task id → `"<group>=<STATE>"` entries) does no I/O, mirroring
  `tail_held_out_task_ids`, so it is unit-testable without a repo.

## Risks / Trade-offs

- A run whose only remaining work is a held tail task ends with `integrate_complete: true`
  and pending tail work, which is the same shape as today's "tail outstanding" journal; the
  new `pending_tail_blocked` field plus the `NOTE` line distinguish "blocked" from "not yet
  run". The dashboard reads the journal directly, so no separate dashboard change is required
  for the field to be visible in raw form.
