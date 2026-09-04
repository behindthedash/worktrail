## 1. Re-review rounds carry the prior round's findings forward (`Re-review rounds carry the prior round's findings forward`)

- [ ] 1.1 Implement requirement: In `src/worktrail/orchestrator/dispatch.py`, extend
      `apply_report`'s existing `if role == ROLE_REVIEW and report.get("review_status"):`
      block (design.md D1) to also stash `report.get("critical_issues")`,
      `report.get("major_issues")`, and `report.get("notes")` onto the task under
      `review_critical_issues`, `review_major_issues`, `review_notes`. Then, in
      `build_worker_prompt`, compute a `round_awareness` string (design.md D2): empty
      when `task.get("retry_count", 0) == 0`, else a clause naming the round number
      (`task["retry_count"] + 1`) and the task's stashed `review_critical_issues`/
      `review_major_issues`/`review_notes`, instructing the reviewer to mark each
      previously-reported issue Resolved or Still Present before listing anything new.
      Insert it into `_ROLE_ACTION[ROLE_REVIEW]` immediately after the existing
      `"{review_checklist}"` slot (before the `Write ... review.md` sentence), and pass
      it as a new `.format()` kwarg alongside the existing ones. Round-1 prompts
      (`retry_count` absent or 0) must render byte-identical to today. Add regression
      tests in `tests/orchestrator/test_dispatch.py` in the same task: (a)
      `build_worker_prompt(ROLE_REVIEW, task, ctx)` for a task with no `retry_count`
      contains no round-number or prior-findings text; (b) after `apply_report` applies a
      `ROLE_REVIEW` report with `critical_issues: 1`, `major_issues: 2`, `notes: "missing
      null check in parse()"`, calling `build_worker_prompt(ROLE_REVIEW, task, ctx)` again
      for the same task renders round 2, the 1/2 counts, and the notes text, with
      instructions to mark each Resolved or Still Present before new findings; (c) a
      `PASSED` `ROLE_REVIEW` report still stashes `review_critical_issues`/
      `review_major_issues`/`review_notes` via `apply_report` (its own counts, typically
      0/0), per spec.md's "PASSED or skipped review" scenario.

## 2. Escalation records the full round-by-round history (`An escalated task's journal entry records every review round`)

- [ ] 2.1 Implement requirement: In `src/worktrail/orchestrator/live.py`'s
      `_apply_step_commit` (design.md D3), inside the existing
      `if new in ("escalated", "failed"):` block, when `new == "escalated"` build a
      `convergence_summary` list from `entries` (this run's accumulated journal list):
      one item per prior journal entry with `e.get("task") == task["id"] and e.get("role")
      == dispatch.ROLE_REVIEW`, each `{"round": i + 1, "review_status": ...,
      "critical_issues": ..., "major_issues": ..., "notes": ...}` read from that entry's
      `report` dict, followed by one final item for the report producing this entry
      itself. Stamp `entry["convergence_summary"] = convergence_summary` on the entry
      being built (mirroring the existing conditional-stamp style of
      `if task.get("_scope_added_files"):` in the same function). Do not stamp it for the
      `new == "failed"` branch of the same `if`. Add regression tests in the same task
      (new file `tests/orchestrator/test_live_review_convergence_summary.py`,
      following the injected-spawn hermetic pattern in
      `tests/orchestrator/test_live_run_circuit_breaker_terminal_status.py`): drive a task
      through three `FAILED` review rounds against the sample-spec fixture (`only=`
      constrained to one dependency-free task) and assert the journal entry for the third
      (escalating) report has `convergence_summary` with exactly three items in round
      order, the third matching that report's own `review_status`/`critical_issues`/
      `major_issues`/`notes`; and a second test asserting a non-escalating `FAILED` review
      (retry count still below `MAX_REVIEW_RETRIES` after the transition) produces a
      journal entry with no `convergence_summary` key.

## 3. Verification

- [ ] 3.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm it is green, including the
      new tests from sections 1 and 2. Verification-only — no file changes expected.
- [ ] 3.2 [cleanup] Run `python3 -m worktrail.orchestrator.orchestrate check` (golden
      record/replay regression) and confirm it is unaffected by the round-1 prompt text
      staying byte-identical. Verification-only — no file changes expected.
- [ ] 3.3 [cleanup] Run `openspec validate orchestrator-review-loop-bounded-convergence
      --strict` and confirm it passes. Verification-only — no file changes expected.

