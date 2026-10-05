## Why

The committed live evaluator answer records its agent and capture date but not the evaluator
prompt revision that produced it. A replay can therefore continue to look valid after the prompt
has changed, without making the mismatch visible or requiring a fresh recording.

## What Changes

- Record a deterministic fingerprint of the evaluator prompt template in the smoke harness's
  metadata alongside the agent and capture date.
- Require the offline replay to verify that the committed recording's fingerprint matches the
  current prompt template, so a prompt edit makes re-recording an explicit decision.
- Cover fingerprint generation and recording provenance without invoking a live evaluator.

## Capabilities

### New Capabilities

### Modified Capabilities

- `triage-evaluator-live-smoke`: recorded evaluator answers identify and are checked against the
  prompt template revision that produced them.

## Impact

- `src/worktrail/workqueue/triage_smoke.py`: derives and writes the prompt fingerprint.
- `tests/workqueue/test_triage_smoke.py` and
  `tests/workqueue/test_queue_triage_live_replay.py`: cover the metadata and mismatch failure.
- `tests/fixtures/triage_evaluator_answers.json`: gains the fingerprint when regenerated or
  updated as recorded fixture data.
