## 1. Widen Epic Citation Matching

- [x] 1.1 Add regression coverage in `tests/workqueue/test_seed_backlog.py` for "Epic NNN
  Feature M" prose citation, future-spec-id citation, and a bare epic-number-mention negative
  case, then implement `_epic_citation_patterns()` and widen `_citing_spec_ids()` in
  `src/worktrail/workqueue/seed_backlog.py` to match any of: the literal epic id, "Epic NNN
  Feature M" prose, or a documented future spec id. Update `find_epic_gaps()`'s call site to
  pass precompiled patterns. Preserve existing scan behavior (both spec formats, archived
  changes, folder-name dedup) and the existing literal-epic-id match. (Requirement: Epics with
  unspecced features are seeded by citation gap)

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_seed_backlog.py` and confirm
  all citation-matching tests (existing and new) pass; depends on 1.1.
- [x] 2.2 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
  worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends on
  2.1.
