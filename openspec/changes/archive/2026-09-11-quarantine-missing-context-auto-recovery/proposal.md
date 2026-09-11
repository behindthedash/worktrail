## Why

A task worker that cannot find a sibling task's file in its worktree reports
that path in `missing_context` and fails. When the sibling's work has already
merged to base, the failure is not a code defect at all: the task's stacked
worktree was simply forked before the dependency landed because
`worktrail-compile` never inferred the edge. Today the orchestrator's only
consumer of a failed report's `missing_context` is scope escalation
(`_scope_escalation_files`, design D5 of `fix-scope-escalation`), which only
considers paths that already exist as files in the task worktree, so this case
falls through to a terminal `failed` and the group is quarantined.

Recovery is then a fixed hand sequence: read the journal, confirm the file is
on base, delete the stale worktree and branch, run `worktrail-live clear-task`,
relaunch `full-real`. That sequence was performed by hand again on
2026-09-10 (devops `devops-bin-sync-doc-drift-guard`, run `go-20260910-215728`),
the fifth or more documented instance. Each prior fix (prose references #1044,
Python imports #1121, TS/JS #1141, dynamic imports #1146) closed one inference
gap; the class of gap is open-ended and inference will never reach full
coverage. The worker's own report already carries enough evidence to
self-heal at the orchestrator level regardless of which gap caused it.

## What Changes

- Add a missing-context auto-recovery check to the live orchestrator's
  report-application path: when a report would drive a task to a terminal
  status and its `missing_context` names a path that is declared by another
  task in the same RunPlan, is absent from the task's worktree, and exists
  with non-empty content on the live base ref, the task is recovered instead
  of failed.
- Recovery performs the manual sequence in-process: remove the task's
  worktree and branch, reset the task to pending with its strike count
  cleared, and let the frontier scheduler re-dispatch it from a fresh stacked
  worktree forked from the current base. It fires at most once per task per
  run and never cascades into quarantine on its own.
- Journal the recovery as an event entry carrying the triggering report, the
  recovered paths, the sibling task ids, the base ref, and
  `category: orchestrator_defect`, so the intervention is auditable and
  resume replays it faithfully.
- Scope escalation is unchanged; the two checks are mutually exclusive per
  path (escalation requires the path to exist in the worktree, recovery
  requires it to be absent) and recovery takes precedence for a report that
  qualifies.

## Capabilities

### New Capabilities

- `missing-context-auto-recovery`: worker-report-driven detection and one-shot
  re-dispatch of a task whose failure names a sibling's file that has since
  merged to base.

### Modified Capabilities

- None. `fix-scope-escalation` keeps its requirements; this change adds a
  disjoint pre-check in front of it.

## Impact

- `src/worktrail/orchestrator/live.py`: one validation helper beside
  `_scope_escalation_files`, wiring at both `drive()` report sites that call
  `_commit_step`, worktree/branch teardown, a new journal event, and replay
  handling on resume.
- Tests under `tests/orchestrator/` reproduce the observed failure with a
  real git fixture (sibling merged to base, task worktree forked before it)
  and prove one recovery, one re-dispatch, no quarantine, once-only
  behaviour, and the negative cases (path not on base, path not a sibling's,
  path present in worktree, empty blob).
- No change to worker prompts: `dispatch.build_worker_prompt` already
  requires absent files to be listed in `missing_context`.
- No change to `worktrail-compile`; the inference work stays valuable and is
  now backstopped rather than replaced.
