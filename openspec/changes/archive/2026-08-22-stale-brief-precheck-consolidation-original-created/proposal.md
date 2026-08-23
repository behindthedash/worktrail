## Why

`worktrail-check-brief-staleness` bounds its history search using a brief's `created:`
timestamp. For a consolidated brief, `consolidate_cluster.py` stamps `created:` at
consolidation time, not at the earliest source brief's true creation time. Any PR or commit
that landed between the true original creation and the later consolidation is wrongly
excluded as "merged before creation" — even when it already resolved part of the
consolidated brief's scope. Verified live: consolidated brief `20260813-115343-consolidated-8376e7`
(consolidated 2026-08-13 from two 2026-08-05 source briefs) silently excluded PR #11 (merged
2026-08-06, mailbox-service), which had already fully resolved one of its two items.

## What Changes

- `draft_consolidated_brief()` in `consolidate_cluster.py` reads each resolvable member's
  `created:` frontmatter (handling both quoted-string and PyYAML-native-datetime forms) and
  tracks the earliest one across all members, returned as `original_created` in the draft.
- `_build_consolidated_brief_content()` writes a new `original-created:` frontmatter field on
  the consolidated brief (in addition to the existing `created:` field, which keeps recording
  true consolidation time) when an earliest original timestamp was found.
- `check_brief_staleness.py`'s `_read_brief()` prefers `original-created:` over `created:` as
  the `since` value passed to `check()` when `original-created:` is present, falling back to
  `created:` otherwise. A non-consolidated brief carries no `original-created:` field and is
  unaffected.
- The `stale-brief-precheck` spec's "History Search Is Bounded By The Brief's Capture Time"
  requirement documents that the search boundary reads `original-created:` when present,
  falling back to `created:`, with a scenario covering a consolidated brief whose
  `original-created:` predates its `created:`.

## Capabilities

### Modified Capabilities
- `stale-brief-precheck`: the search-boundary requirement now documents reading
  `original-created:` in preference to `created:` when a brief carries both fields (the
  consolidated-brief case).

## Impact

- `src/worktrail/router/consolidate_cluster.py`: `draft_consolidated_brief()`,
  `_build_consolidated_brief_content()`.
- `src/worktrail/router/check_brief_staleness.py`: `_read_brief()`.
- `openspec/specs/stale-brief-precheck/spec.md`: one requirement's text and scenario list.
- `tests/router/test_consolidate_cluster.py`, `tests/router/test_check_brief_staleness.py`:
  new coverage for the propagation and fallback-preference behavior.
- No CLI flags, public function signatures beyond return-dict/frontmatter shape, or storage
  layout changes beyond the new frontmatter field.
