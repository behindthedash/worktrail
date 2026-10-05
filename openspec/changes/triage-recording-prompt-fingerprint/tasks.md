## 1. Capture deterministic prompt provenance

- [ ] 1.1 Derive a deterministic SHA-256 fingerprint from the canonical UTF-8 evaluator prompt
      template, expose that single value for recording and replay, and add it to `record_run()`'s
      `_meta` block without changing the raw evaluator response or making any additional live
      call. Extend the injected-evaluator unit tests to assert the value is present and changes
      when the template input under test changes. (Requirement: A hand-run smoke harness records
      a real evaluator run)
      files: src/worktrail/workqueue/triage_smoke.py tests/workqueue/test_triage_smoke.py

## 2. Enforce fixture provenance offline

- [ ] 2.1 Add the current deterministic prompt fingerprint to the committed recorded fixture and
      extend its offline replay provenance test to require the field and compare it to the shared
      current-template value. Cover both missing and mismatched fingerprints as assertion
      failures, without calling an evaluator, network service, or credential. (Requirement:
      Offline replay verifies prompt provenance)
      files: tests/fixtures/triage_evaluator_answers.json tests/workqueue/test_queue_triage_live_replay.py

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_triage_smoke.py
      tests/workqueue/test_queue_triage_live_replay.py`, then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate triage-recording-prompt-fingerprint --strict` and
      `worktrail-compile openspec/changes/triage-recording-prompt-fingerprint`.
      depends: 2.1
