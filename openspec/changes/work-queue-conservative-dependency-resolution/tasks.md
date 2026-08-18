## 1. Conservative Runtime Resolution

- [ ] 1.1 Add focused resolver and consumer regressions in `tests/workqueue/test_work_queue.py`, then implement shared dependency-reference shape checking and structured `done`, `active`, `stale`, `ambiguous`, and `malformed` classification in `src/worktrail/workqueue/work_queue.py` (and, if extracted to prevent contract drift, a focused helper under `src/worktrail/workqueue/` plus its producer import in `src/worktrail/workqueue/create_handoff.py`). Route satisfaction, blocked-state, and claim-warning behavior through that result; preserve raw values and candidates; keep only done/stale satisfied; cover queue, picked, done, valid stale, ambiguous, non-string/blank/comma malformed states, state-bearing warnings, byte-for-byte non-mutation, and the comma-joined incident with the first embedded dependency still active. (Requirement: Runtime dependency resolution distinguishes reference states) (Requirement: Eligibility fails closed except for done and valid stale references) (Requirement: Runtime diagnostics identify unresolved dependency values) (Requirement: Runtime resolution does not mutate queue briefs)

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_create_handoff.py tests/workqueue/test_work_queue.py` and confirm producer-contract compatibility plus focused runtime regressions pass; depends on 1.1.
- [ ] 2.2 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends on 2.1.
