## 1. Reviewer contract and predicate (`dispatch`)

- [ ] 1.1 In `src/worktrail/orchestrator/dispatch.py`, add
      `review_names_decision(report) -> str | None` next to `transition`: return a non-empty
      `decision_required` string, else `notes` when it matches (case-insensitive)
      `planner/human decision`, `human decision`, or `planner decision`, else `None`. Extend
      the `ROLE_REVIEW` report-back JSON schema line with
      `"decision_required": "<text>|null"`, and extend `_round_awareness_clause` (round ≥ 2
      only) to instruct the reviewer to set it solely for a Still Present
      AC-vs-existing-behaviour conflict a planner/human must resolve, citing the AC and the
      conflicting test/behaviour. Round-1 prompt text must not change.
      (Requirement: A review report can name a planner/human decision.)
      Add `tests/orchestrator/test_dispatch_decision_marker.py` covering the four spec
      scenarios: structured field wins, phrase-in-notes fallback, ordinary notes return
      `None`, and the round-2 prompt names `decision_required` while the round-1 prompt does
      not contain it (assert against `build_worker_prompt(ROLE_REVIEW, ...)` with
      `retry_count` 0 and 1).
      files: src/worktrail/orchestrator/dispatch.py, tests/orchestrator/test_dispatch_decision_marker.py

## 2. Early breaker and decision filing (`live`)

- [ ] 2.1 [depends: 1.1] In `src/worktrail/orchestrator/live.py`, give `_apply_step_commit`
      keyword-only `repo: Path | None = None`, `spec_rel: str | None = None`,
      `run_id: str | None = None`, and pass them from `live_run_real`'s `_commit_step`. After
      `dispatch.apply_report` (beside the Design D5 `_scope_pending` override): if
      `role == ROLE_REVIEW`, `new == "fixing"`, `task["retry_count"] >= 2`, and
      `dispatch.review_names_decision(rep)` is non-None, set `new = "escalated"` and
      `task["status"] = "escalated"` so the existing escalated block stamps `terminal_status`
      and `convergence_summary`; additionally set `entry["escalation_reason"] =
      "decision-required"` and `entry["pending_decision"]` to a
      `decisions.pending_decision_envelope(...)` with `source="orchestrator-review-loop"`,
      `repo=str(repo)`, `subject=f"{spec_rel}/{task_id}"`, `run_id`, the decision text as
      `question`, the two fixed options, and `decision_id =
      decisions.decision_identity(...)`. Then call `decisions.ask(...)` best-effort (wrapped;
      log and continue on any exception or when `repo`/`spec_rel` is None) with the record
      fields the spec names, the resume recipe in `context`, and the reviewer's text as
      `recommendation`. Import the decision primitives lazily the way
      `dispatch._decision_helpers` does.
      (Requirements: A repeated decision-naming finding escalates early with a pending
      decision; The decision-required escalation files an idempotent decision record.)
      Add `tests/orchestrator/test_live_review_decision_breaker.py` following the
      injected-spawn hermetic pattern of `test_live_review_convergence_summary.py`
      (throwaway git repo, `live_run_real`, scripted review/fix reports, `WORK_QUEUE_DIR`
      pointed at a tmp dir). Cover: second FAILED review with `decision_required` escalates
      with `escalation_reason`, two-round `convergence_summary`, envelope
      `question`/`provenance.subject`, and no third fix spawn; round-1 decision-naming FAILED
      still routes to `fixing` with no `pending_decision`; ordinary repeated FAILED reviews
      escalate on round 3 with no `escalation_reason`; resume from the escalated journal
      re-dispatches nothing and files no second record; the record lands in
      `decisions/open/` with matching id, `source`, `run-id`, and two options; an unwritable
      `WORK_QUEUE_DIR` leaves status and envelope intact; a second `ask` for the same
      identity converges on one record.
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_live_review_decision_breaker.py

## 3. Operator docs

- [x] 3.1 [depends: 1.1] In `skills/worktrail-go/references/decision-queue.md`, under the
      envelope section, list `orchestrator-review-loop` as a decision source alongside
      `check_spec_collision`: what fires it (round-2 repeated AC-vs-existing-behaviour
      finding), what the record cites, and the resume recipe (`worktrail-live clear-task`
      then resume). Keep existing `{#anchors}` unchanged.
      files: skills/worktrail-go/references/decision-queue.md

## 4. Verification

- [ ] 4.1 [depends: 2.1, 3.1] [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator`,
      then `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` (golden replay must be unchanged), and
      `PYTHONPATH=src pytest -q tests/test_plugin_surface.py`. Then, as the brief requires,
      exercise a real orchestrator run: scaffold a throwaway fixture change whose one task's
      AC contradicts an existing test in the fixture repo (mirror the
      `restrict-agents-md-shim-check-to-git-repos` 1.1 conflict), run it with
      `WORK_QUEUE_DIR` set to a scratch dir, and confirm the task escalates after review
      round 2 with `escalation_reason: decision-required`, a record appears in
      `decisions/open/`, and non-dependent groups complete. Record the observed round and
      spawn counts in the PR description. Finally run
      `openspec validate review-loop-repeated-finding-to-decision --strict` and
      `worktrail-compile openspec/changes/review-loop-repeated-finding-to-decision`.
