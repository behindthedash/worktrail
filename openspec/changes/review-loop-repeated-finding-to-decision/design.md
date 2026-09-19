## Context

`_apply_step_commit` (live.py) journals every step report under `state_lock` and applies
`dispatch.apply_report`, which routes a `FAILED` review to `fixing` until `retry_count`
reaches `MAX_REVIEW_RETRIES` (3), then to `escalated`. Escalation already does the right
thing downstream: the task is terminal, the group is quarantined, dependents are blocked by
the dependency gate, and other groups proceed. What is missing is (a) stopping one round
earlier when the reviewer has said the remaining finding is not a code defect, and (b)
turning that stop into a structured, answerable question rather than a `convergence_summary`
the human must decode. The Design D5 `_scope_pending` override in the same function is the
precedent for "the caller decides the transition, then the normal journaling runs".

## Goals / Non-Goals

- Goals: fire on the *second* consecutive FAILED review that names a planner/human decision;
  reuse the `escalated` terminal status so resume, replay, quarantine, `clear_tasks`, and
  dashboards need no new state; file one idempotent decision record with the conflicting
  AC and test cited; keep round-1 prompts and every non-decision run byte-identical.
- Non-Goals: automatic resume of the task from an answered decision (the operator amends the
  AC or the test, clears the task, and resumes — the same manual path used today, now
  spelled out in the record); structural diffing of findings across rounds (the report
  carries counts and free-text `notes`, and the reviewer is already asked to mark each prior
  finding Resolved/Still Present); firing on round 1 (a fix round may legitimately satisfy
  both the AC and the existing test, as the worker tried to here).

## Decisions

- **Structured field first, phrase match second, one predicate.** The reviewer prompt asks
  for `decision_required: "<text>"|null` in the report-back JSON. `review_names_decision`
  returns that text when present; otherwise it returns `notes` when `notes` matches a
  case-insensitive `planner/human decision` / `human decision` / `planner decision` phrase
  (the exact wording in the evidence). Returning `None` otherwise keeps the hot path a
  two-key lookup. The phrase fallback exists so an older-prompt reviewer, or one that
  ignores the schema and writes prose, still trips the breaker; the field exists so the
  orchestrator never has to parse the finding itself.
- **Trigger = `role == ROLE_REVIEW` and `review_status == FAILED` and post-transition
  `retry_count >= 2` and predicate non-None.** `retry_count >= 2` after the transition means
  this is at least the second FAILED review, so one fix round has already failed to
  reconcile the conflict. The override happens in `_apply_step_commit` right after
  `apply_report` (next to the `_scope_pending` override) and only when `new == "fixing"` —
  the round-3 breaker already returns `escalated` and is left alone.
- **Reuse `escalated`; mark the reason on the entry.** `new = "escalated"`,
  `task["status"] = "escalated"`, then the existing `new == "escalated"` block stamps
  `terminal_status` and `convergence_summary`. The entry additionally gets
  `escalation_reason: "decision-required"` and `pending_decision: <envelope>`. Replay
  (`reconcile_from_journal`) already restores `escalated` from `terminal_status`, so a resume
  neither re-fires the breaker nor re-runs the task.
- **Envelope built the `check_spec_collision` way.** `GUARD_SOURCE =
  "orchestrator-review-loop"`, `subject = f"{spec_rel}/{task_id}"`, `repo = str(repo)`,
  `question = decision text`, `run_id` from `live_run_real`, `dispatch_mode` omitted (the
  orchestrator does not know it). Identity via `decision_identity(source, repo, subject,
  question)` so an unchanged conflict on re-run converges on one record. Options are fixed
  strings: `amend-ac: relax the acceptance criterion to admit the existing behaviour` and
  `keep-ac: keep the criterion literal and change the conflicting test/behaviour`; the
  reviewer's text is the recommendation, not an option, so identity stays stable.
- **Filing is best-effort and outside the hot path's failure surface.** `decisions.ask()`
  runs inside the same `state_lock` window (it is a few file writes under
  `$WORK_QUEUE_DIR`), wrapped so any exception — missing queue dir, duplicate open decision
  for the subject, import failure — logs and continues; the journal entry keeps its envelope
  either way, mirroring how `check_spec_collision` degrades. `ask()` is called with
  `decision_id` set to the envelope's id, no `brief`, and `context` naming the review rounds,
  the conflicting AC/test from the decision text, and the resume recipe
  (`worktrail-live clear-task --tasks <id>` then resume).
- **Context plumbing.** `_apply_step_commit` gains keyword-only `repo`, `spec_rel`, and
  `run_id` (all optional, default `None`); `live_run_real`'s `_commit_step` passes them.
  The other caller (the `_apply_skip_review_commit`/replay side) passes nothing and can
  never reach the breaker because a skipped review is never `FAILED`. When `repo` or
  `spec_rel` is `None` the breaker still escalates but files no record (identity needs
  non-blank provenance), and logs why.

## Risks / Trade-offs

- A reviewer that sets `decision_required` on a real code defect stops the loop one round
  early. Mitigation: the prompt scopes the field to Still-Present AC-vs-existing-behaviour
  conflicts, the breaker only fires after one fix round has already failed, and the human
  sees the reviewer's own text and can answer "keep-ac" to send it back.
- Phrase matching on `notes` is a heuristic. It is deliberately narrow (three fixed phrases)
  and only consulted on round ≥ 2 of a FAILED review, where the cost of a false positive is
  one saved round and a decision record the human dismisses.
- The `orchestrate check` golden replay must stay green: no cassette contains a round-2
  FAILED review naming a decision, and round-1 prompts are unchanged, so no golden diff is
  expected; verification runs it anyway.
