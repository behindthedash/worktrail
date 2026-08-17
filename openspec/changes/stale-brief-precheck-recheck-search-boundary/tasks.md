## 1. Prefer `released-at:` in the staleness check's search boundary

- [ ] 1.1 In `check_brief_staleness.py`'s `_read_brief()`, read `released-at:` off the brief's
      frontmatter (via the already-imported `read_frontmatter`) and use it as the `since`
      value passed back to the caller when present and non-empty, ahead of the existing
      `original-created:` then `created:` fallback chain: `fm.get("released-at") or
      fm.get("original-created") or fm.get("created")`.
      files: src/worktrail/router/check_brief_staleness.py

## 2. Spec and tests

- [ ] 2.1 Confirm `openspec/specs/stale-brief-precheck/spec.md`'s "History Search Is Bounded By
      The Brief's Capture Time" requirement (via this change's delta spec) documents the full
      `released-at:` > `original-created:` > `created:` boundary precedence and the rechecked-
      brief scenario.
      files: openspec/specs/stale-brief-precheck/spec.md
- [ ] 2.2 Add unit tests in `tests/router/test_check_brief_staleness.py` covering:
      `_read_brief()` returns `released-at:`'s value as `since` when the brief carries that
      field (ahead of both `original-created:` and `created:`); a brief with only
      `original-created:`/`created:` (no `released-at:`) still returns the existing
      precedence unchanged; an end-to-end `check()` call against a brief carrying
      `released-at:` correctly excludes a commit merged between `created:` and `released-at:`.
      files: tests/router/test_check_brief_staleness.py
- [ ] 2.3 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_check_brief_staleness.py`, then
      the full suite (`PYTHONPATH=src pytest -q`) and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`.
