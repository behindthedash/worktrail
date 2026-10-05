## Why

PR #1305 shipped `work-directly` span correction -- an evaluator may attach `refuted_span`
(optionally with `corrected_span`) to a `work-directly` verdict, `apply` rewrites the brief's
focus text and appends a `## Triage` section, and a span covering the whole focus downgrades the
verdict to `keep`. Every one of those rules is enforced by code that only ever sees a `Verdict`
object somebody else built.

Confirmed via `grep -rn "refuted_span" tests/workqueue/test_queue_triage.py` (2026-09-20): every
coverage point is one of three things -- a hand-built `Verdict(... refuted_span=...)`
(lines 6079, 6184, 6205, 6266, 6287), a raw-JSON parse round-trip through `parse_verdicts()`
(lines 282-351), or a substring assertion against `EVALUATOR_PROMPT_TEMPLATE`
(lines 835-858, and `tests/workqueue/test_queue_triage_inventory.py:270`). No test anywhere
invokes the real evaluator.

That leaves the one link in the chain nobody has checked: whether a real model, given
`EVALUATOR_PROMPT_TEMPLATE`'s Step 2b (`src/worktrail/workqueue/queue_triage.py:225`), actually
emits `work-directly` with a `refuted_span` copied *verbatim* from the focus text. It is a
verbatim-quoting requirement, which is exactly the instruction models are worst at: a span off by
a quote character, a re-wrapped line, or a paraphrase parses fine and then silently fails
`_span_is_rewritable()`, so the correction is dropped and the operator sees an ordinary
`work-directly` with no sign anything was lost. A prompt-text assertion cannot detect that; only
a real run can.

A live model call can never be part of `pytest` -- the suite is hermetic by construction and CI
runners have no credential. This repo already has the shape for exactly this problem: the risk
judgment is exercised offline by replaying *recorded* answers (`tests/fixtures/risk_probes.json`
+ `risk_probe_answers.json`, replayed by `tests/router/test_risk_judgment.py`), with re-recording
a deliberate hand-run step. The evaluator gets the same treatment.

## What Changes

- New hand-run harness `src/worktrail/workqueue/triage_smoke.py` (`python3 -m
  worktrail.workqueue.triage_smoke`, no console script -- it is an operator recording tool, like
  `scripts/regenerate_classifier_corpus.py`, not part of any skill's command surface). It builds a
  fixture intake brief in a throwaway `$WORK_QUEUE_DIR` whose focus carries one refutable claim
  plus otherwise-valid, directly-actionable work, runs the **real** `evaluate_group()` against
  this repo's checkout, and writes the untouched `raw_text` plus the exact focus text it was shown
  to `tests/fixtures/triage_evaluator_answers.json`, with the model/agent and capture date in a
  `_meta` block.
- The harness then adjudicates the recorded run against Step 2b's contract and prints a verdict:
  the model emitted `work-directly`; it set `refuted_span`; and that span is a verbatim substring
  of the focus it was shown. A miss is reported as a miss -- the harness exits non-zero and names
  which of the three failed, so "the model ignored the span rule" is never mistaken for a clean
  run.
- New offline replay test `tests/workqueue/test_queue_triage_live_replay.py` feeds the *recorded*
  `raw_text` through the production `parse_verdicts()` and `apply_verdicts()` against a real brief
  file, asserting the focus is rewritten and a `## Triage` section appended. It also replays the
  same recorded verdict with its span widened to the entire focus text and asserts the downgrade
  to `keep`. No network, no credential: every CI runner exercises the path a real model's output
  actually takes.
- `EVALUATOR_PROMPT_TEMPLATE`'s Step 2b is tightened only if the recorded run shows the model
  omitting or misquoting the span -- the recording decides, not a guess made in advance.

## Capabilities

### New Capabilities
- `triage-evaluator-live-smoke`: a recorded real-evaluator run proves `work-directly` span
  correction works end to end, and is replayed offline.

### Modified Capabilities

## Impact

- `src/worktrail/workqueue/triage_smoke.py` (new), `src/worktrail/workqueue/queue_triage.py`
  (Step 2b wording, only if the recording shows a miss).
- `tests/workqueue/test_triage_smoke.py` (new), `tests/workqueue/test_queue_triage_live_replay.py`
  (new), `tests/fixtures/triage_evaluator_answers.json` (new, recorded).
- No behavior change for any existing caller: the harness is opt-in and hand-run, and nothing in
  `pytest` or CI makes a live model call.
