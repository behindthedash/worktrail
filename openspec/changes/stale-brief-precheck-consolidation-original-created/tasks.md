## 1. Propagate the earliest original `created:` through consolidation

- [ ] 1.1 In `consolidate_cluster.py`, add a helper that reads a member's `created:` frontmatter
      value (preferring that member's own `original-created:` if present, else `created:`),
      going around `_parse_frontmatter()`'s scalar-only filter so a PyYAML-native
      `datetime.datetime`/`datetime.date` value is not dropped, and returns a UTC-aware
      `datetime` (or `None` on missing/unparseable input) — degrade, never raise, matching this
      module's existing defensive style.
- [ ] 1.2 In `draft_consolidated_brief()`, call that helper for each resolvable member while
      building `members`, track the earliest parsed timestamp across all members, and include
      it on the returned dict as `original_created` (an ISO-8601 string, or omitted/`None` when
      no member's timestamp parsed).
- [ ] 1.3 In `_build_consolidated_brief_content()`, when `draft.get("original_created")` is
      present, write it as an additional `original-created:` frontmatter line alongside the
      existing `created:` line (which continues to record true consolidation time via
      `now.isoformat(...)`).

## 2. Prefer `original-created:` in the staleness check's search boundary

- [ ] 2.1 In `check_brief_staleness.py`'s `_read_brief()`, read `original-created:` off the
      brief's frontmatter (via the already-imported `read_frontmatter`) and use it as the
      `since` value passed back to the caller when present and non-empty, falling back to
      `created:` exactly as today otherwise.

## 3. Spec and tests

- [x] 3.1 Confirm `openspec/specs/stale-brief-precheck/spec.md`'s "History Search Is Bounded By
      The Brief's Capture Time" requirement (via this change's delta spec) documents the
      `original-created:`-preferred-over-`created:` boundary computation and the consolidated-
      brief scenario.
- [ ] 3.2 Add unit tests in `tests/router/test_consolidate_cluster.py` covering: a multi-member
      draft whose members have different `created:` timestamps yields `original_created` equal
      to the earliest one; a member with a native-PyYAML-datetime `created:` value contributes
      correctly; a member with a missing/unparseable `created:` is skipped without raising; the
      written consolidated brief's frontmatter carries `original-created:` when the draft has
      it, and omits the field (unchanged from current behavior) when it does not.
- [ ] 3.3 Add unit tests in `tests/router/test_check_brief_staleness.py` covering: `_read_brief()`
      returns `original-created:`'s value as `since` when the brief carries that field; a brief
      with only `created:` (no `original-created:`) still returns `created:`'s value as `since`,
      unchanged from current behavior.
- [ ] 3.4 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_consolidate_cluster.py
      tests/router/test_check_brief_staleness.py`, then the full suite
      (`PYTHONPATH=src pytest -q`) and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`.
