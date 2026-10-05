## 1. Live evaluator smoke

- [x] 1.1 Add `src/worktrail/workqueue/triage_smoke.py`: a hand-run harness exposing
      `build_fixture_brief(queue_root)` (writes one intake brief whose focus carries a single
      refutable claim about this repo plus otherwise-valid directly-actionable work),
      `record_run(...)` (invokes `queue_triage.evaluate_group()` -- injectable, defaulting to the
      real one -- and returns the recording dict: `raw_text`, the shown focus text, the brief id,
      and a `_meta` block with agent and capture date), `adjudicate(recording)` (returns the list
      of failed conditions among: verdict is `work-directly`; `refuted_span` is set; the span is a
      verbatim substring of the shown focus), and `main(argv)` writing the recording to
      `tests/fixtures/triage_evaluator_answers.json` and exiting non-zero with the named failures.
      Build the fixture brief under a throwaway queue root, never the operator's real
      `$WORK_QUEUE_DIR`. No console script and no skill reference -- it is run as
      `python3 -m worktrail.workqueue.triage_smoke`, like
      `scripts/regenerate_classifier_corpus.py` is run by hand. Document the hand-run recording
      step and the "never in pytest" rule in the module docstring.
      (Requirements: A hand-run smoke harness records a real evaluator run; The harness
      adjudicates the recorded run against the Step 2b contract.)
      Add `tests/workqueue/test_triage_smoke.py` covering the harness offline with an injected
      fake `evaluate_group` (never a live call): the fixture brief lands under the throwaway root
      with a focus containing the refutable claim; the recording captures `raw_text` verbatim plus
      the shown focus and `_meta`; `adjudicate` passes a clean run and names exactly the failing
      condition for each of a non-`work-directly` verdict, an omitted span, and a span that is not
      a verbatim substring; and `main` exits non-zero on a miss and zero on a pass.
      files: src/worktrail/workqueue/triage_smoke.py, tests/workqueue/test_triage_smoke.py

- [x] 1.2 [depends: 1.1] Record the live run and replay it offline. Run
      `python3 -m worktrail.workqueue.triage_smoke` against this repo with a real evaluator agent,
      committing the produced `tests/fixtures/triage_evaluator_answers.json` as recorded (never
      hand-authored) data. If the harness reports a miss -- an omitted or misquoted span -- tighten
      `EVALUATOR_PROMPT_TEMPLATE`'s Step 2b span rules in
      `src/worktrail/workqueue/queue_triage.py` and re-record until it passes, committing only the
      passing recording; if the first run already passes, leave the prompt text untouched. Note the
      outcome (prompt changed or not, and why) in the PR description.
      (Requirements: Step 2b is tightened only on recorded evidence.)
      Add `tests/workqueue/test_queue_triage_live_replay.py`, which loads the committed fixture and
      replays it with no network and no credential: feed the recorded `raw_text` through
      `parse_verdicts()` and `apply_verdicts()` against a real brief file carrying the recorded
      focus, asserting the refuted span is gone from the focus (replaced by `corrected_span` when
      one was recorded) and a `## Triage` section describing the rewrite was appended; then replay
      the same recorded verdict with `refuted_span` widened to the brief's entire focus and assert
      the downgrade to `keep` with the focus left unchanged. Reuse the queue fixture builders in
      `tests/workqueue/test_work_queue.py` rather than rolling new ones, and assert the fixture's
      `_meta` names an agent and a capture date so a hand-authored file fails.
      (Requirements: The recorded run is replayed offline through parse and apply; The replay
      covers the whole-focus downgrade.)
      files: tests/fixtures/triage_evaluator_answers.json, tests/workqueue/test_queue_triage_live_replay.py, src/worktrail/workqueue/queue_triage.py

## 2. Verification

- [x] 2.1 [depends: 1.2] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_triage_smoke.py
      tests/workqueue/test_queue_triage_live_replay.py`, then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Confirm the full run
      makes no live model call. Run `python3 scripts/ci/ruff_pinned.py check .`,
      `openspec validate live-evaluator-smoke-for-work-directly-spans --strict`, and
      `worktrail-compile openspec/changes/live-evaluator-smoke-for-work-directly-spans`.
