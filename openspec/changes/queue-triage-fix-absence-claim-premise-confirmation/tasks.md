## 1. Fix premise_check.py absence-claim polarity and broaden the reproduction regex, with tests (`Mechanical premise check precedes evaluation`, `Work-directly converts an intake brief into an execution brief`)

- [ ] 1.1 Implement requirement: in `src/worktrail/workqueue/premise_check.py`: (a) add
      `_ABSENCE_INDICATOR_RE` and `_ABSENCE_WINDOW = 40` (design.md Decision 1); (b) add a
      `polarity: str = "presence"` field to the `Needle` dataclass (Decision 2); (c) in
      `_extract_path_needles()`, compute each path needle's polarity from the 40-character
      window immediately before its match index in `focus` and pass it into the constructed
      `Needle`; (d) update `_check_path()` to accept `polarity` and flip its return only for
      `polarity == "absence"` per Decision 3, leaving the presence branches (not-exists,
      line-count, exists) unchanged; (e) thread `n.polarity` through `run_premise_check()`'s
      `path` branch. In `src/worktrail/workqueue/queue_triage.py`: (f) add
      `|\breproduced\s+via\b` to `_REPRODUCTION_EVIDENCE_RE` (Decision 4). Add regression
      tests in `tests/workqueue/test_premise_check.py`: an absence-claim path needle
      (`"X has no `path/that/does/not/exist.py`"`) confirms true when the path is absent and
      false when it exists; a presence-claim path needle with no absence indicator nearby
      keeps its current confirm-on-exists behavior (existing tests must still pass
      unmodified). Add a regression test in `tests/workqueue/test_queue_triage.py` for
      `_REPRODUCTION_EVIDENCE_RE`/`_work_directly_accepted()`: `evidence="Reproduced via
      pytest tests/foo.py -k bar"` is accepted, matching the existing present-tense case.

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm it is green, including the
      new tests from section 1. Verification-only — no file changes expected.
- [ ] 2.2 [e2e] Run `openspec validate queue-triage-fix-absence-claim-premise-confirmation
      --strict` and confirm it passes. Verification-only — no file changes expected.
