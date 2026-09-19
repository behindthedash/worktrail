## Why

The orchestrator's review/fix loop has exactly one circuit breaker: the Design D3 three-round
escalation in `_apply_step_commit` (`src/worktrail/orchestrator/live.py:4143-4171`), which
stamps `terminal_status: escalated` and rolls a `convergence_summary`. It never looks at
*what* the reviewer found, so a finding that is not a code defect at all — an acceptance
criterion that conflicts with existing behaviour the repo's own tests require — burns every
remaining fix and review round before anyone is told. Evidence (devops run
`go-20260918-155500`, change `restrict-agents-md-shim-check-to-git-repos`, 2026-09-18): task
1.1's AC demanded a literal `(entry/'.git').exists()` predicate; the repo's existing
`test_unreadable_repo_is_logged_and_sweep_continues` required an EACCES carve-out. The worker
implemented the carve-out, the reviewer's round-3 MAJOR-1 still said "reconciling the AC with
the unreadable-repo requirement is a planner/human decision", and the task escalated after
3 fix spawns and 5 review spawns (~$4.59). The group was quarantined, 1.2 blocked on it, and a
human resolved the conflict by hand in ~10 minutes — the same 10 minutes that would have
sufficed after round 2 had the loop stopped and asked.

The decision-queue plumbing needed to ask already exists (`workqueue/decisions.py`'s
`decision_identity`/`pending_decision_envelope`/`ask`, the `check_spec_collision` envelope
builder as the reference pattern, the `pending_user_decision` contract in
`skills/worktrail-go/references/decision-queue.md`). Nothing in the review loop uses it.
(Work-queue brief `20260918-175925-worktrail-orchestrator-when-task-reviewer`.)

## What Changes

- The review report-back gains an optional `decision_required` field: a one-line statement,
  set only when a *Still Present* finding is an AC-vs-existing-behaviour conflict a
  planner/human must resolve, naming the AC and the conflicting test/behaviour. The
  round-awareness clause (round ≥ 2) tells the reviewer when and how to set it. Round-1
  prompts are unchanged.
- A pure predicate `dispatch.review_names_decision(report)` returns the decision text when a
  review report either sets `decision_required` or whose `notes` contain a
  planner/human-decision phrase (the wording the real reviewer used), else `None`.
- `_apply_step_commit` gets a second, earlier circuit breaker: a `FAILED` review on round ≥ 2
  that names a decision routes the task to `escalated` immediately instead of `fixing`. The
  escalating journal entry carries `terminal_status: escalated`, the existing
  `convergence_summary`, plus `escalation_reason: decision-required` and a
  `pending_decision` envelope (schema `worktrail.pending-decision`, source
  `orchestrator-review-loop`, deterministic identity keyed on repo + `<spec>/<task>` + the
  decision text). Journal replay restores the same state from `terminal_status`, as today.
- The orchestrator files the matching decision record via `decisions.ask()` under
  `$WORK_QUEUE_DIR/decisions/open/` — best-effort, never fatal, idempotent on re-run — with
  the AC/test conflict as the question, the review history as context, two concrete options
  (amend the AC to admit the existing behaviour; keep the AC literal and change the
  conflicting test), and the reviewer's text as the recommendation. The record's context
  tells the operator how to resume: fix the AC or test, then `worktrail-live clear-task` the
  task and resume the run.
- Non-dependent groups continue exactly as they do for any escalated task today; the change
  only shortens the path to escalation and makes the escalation answerable.
- `references/decision-queue.md` documents `orchestrator-review-loop` as a decision source.

## Capabilities

### New Capabilities

### Modified Capabilities
- `review-loop-convergence`: a repeated decision-naming review finding escalates on round 2
  and files a pending-decision envelope instead of burning the third fix/review round.

## Impact

- `src/worktrail/orchestrator/dispatch.py` — review report-back schema, round-awareness
  clause, new `review_names_decision` predicate.
- `src/worktrail/orchestrator/live.py` — `_apply_step_commit` early breaker, envelope, and
  best-effort `ask()` filing; `run_id`/repo/spec threaded to it from `live_run_real`.
- `skills/worktrail-go/references/decision-queue.md` — new source documented.
- New tests under `tests/orchestrator/`; existing golden cassettes unchanged (round-1 prompt
  is byte-identical, and the new journal keys only appear on the new escalation path).
- No change to task statuses, `coordinator.FAILED_STATUSES`, `clear_tasks`, or the
  decision-queue CLI. Runs whose reviewers never name a decision behave exactly as before.
