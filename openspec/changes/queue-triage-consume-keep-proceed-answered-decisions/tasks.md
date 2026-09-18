## 1. Consume answered guidance and surface it to the evaluator (`queue-triage`)

- [ ] 1.1 In `src/worktrail/workqueue/queue_triage.py`: add `consume_answered_guidance(path)`
      next to `consume_repo_decision()` — returns `None` unless the brief's `awaiting-decision`
      is `answered`, the envelope loads, the question is not `REPO_ASSIGNMENT_QUESTION`, and
      the answer does not match `_REHOME_DIRECTIVE_RE`; otherwise append a
      `## Triage <date>` note (`verdict: decision-answered`, `decision: <id>`,
      `question: <q>`, `answer: <whitespace-collapsed answer>`), call
      `decisions.resolve_decision()`, and return `{"answered": True, "path", "decision_id",
      "question", "answer"}`. Call it from `group_queue_by_repo()` in both branches whenever
      `consume_repo_decision()` returns `None` (before `infer_repo()` in the repo-less branch);
      the outcome joins neither `inferred` nor `unresolvable`. Add `"decision-answered"` to
      `is_recently_triaged()`'s excluded verdicts. Add `_answered_guidance(path)` returning the
      most recent `decision-answered` note's `(question, answer)` or `None`, and in
      `evaluate_group()` append a `  Human decision: Q: <q> A: <a>` line to a brief's prompt
      entry when present; extend `EVALUATOR_PROMPT_TEMPLATE` with one sentence that a listed
      human decision is settled and `needs-decision` must not re-ask it. Add a
      `PendingDecision(Exception)` carrying `decision_id` and `status` for task 2.1 to raise.
      Update the affected docstrings.
      (Requirement: Answered guidance decisions are consumed; Free-form repo re-home
      decisions are consumed.)
      In `tests/workqueue/test_queue_triage_inventory.py`, alongside the existing
      decision-consumption and `has_unresolved_decision` tests, add one test per spec
      scenario: keep-under-repo answer consumed (note contents, decision archived, link
      cleared, grouped under its repo in the same run); directive-less "Yes, keep it" consumed
      as guidance with repo/group unchanged; `decision-answered` note not counted by
      `is_recently_triaged()`; prompt line carries question/answer and template carries the
      no-re-ask rule; open decision still held with no note; unresolvable canonical answer
      still reported and not consumed as guidance; unknown-repo directive still untouched.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage_inventory.py

## 2. Single-brief gate honours linked decisions (`intake-triage`)

- [ ] 2.1 [depends: 1.1] In `src/worktrail/router/skill_dispatch.py`, `evaluate_single_brief()`:
      always call `consume_repo_decision()` (a resolved outcome sets `resolved_repo` even when
      `repo` was passed), then `consume_answered_guidance()` when it returned `None`, then
      `infer_repo()` only when still repo-less; if `has_unresolved_decision(path)` is still
      true, raise `queue_triage.PendingDecision`. In `main()`'s `--evaluate-brief-triage`
      branch, catch it beside `EvaluatorUnavailable`: print `json.dumps(None)`, write
      `blocked_pending_decision: <id> (<status>)` to stderr, return 2. In
      `skills/worktrail-go/SKILL.md`'s intake-pickup step 1, add the exit-2
      `blocked_pending_decision` case next to `blocked_no_capacity` (do not apply; report the
      pending decision and stop). Update docstrings.
      (Requirement: Interactive single-brief pickup honours linked decisions.)
      In `tests/router/test_skill_dispatch.py`, alongside the existing `evaluate_single_brief`
      tests, add one test per spec scenario: answered keep decision consumed before the
      evaluator spawn with the answer in the prompt and no re-filed decision; open decision
      raises `PendingDecision` and `main()` prints `null` / stderr line / exit 2; answered
      re-home decision overrides the passed repo.
      files: src/worktrail/router/skill_dispatch.py, skills/worktrail-go/SKILL.md, tests/router/test_skill_dispatch.py

## 3. Verification

- [ ] 3.1 [depends: 1.1, 2.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue tests/router`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate queue-triage-consume-keep-proceed-answered-decisions --strict` and
      `worktrail-compile openspec/changes/queue-triage-consume-keep-proceed-answered-decisions`.
