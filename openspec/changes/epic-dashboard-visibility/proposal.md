## Why

`dashboard.py`'s `_NON_SPEC_DIRS` explicitly excludes `docs/specs/epics/` from `scan()`, so an
epic's lifecycle state (no feature decomposition parseable, features outnumber citing specs, or
fully specced/delivered) is entirely invisible to the dashboard — the one place `/go` and
`worktrail-go` render orientation state. Today the only code that computes epic state at all is
`seed_backlog.find_epic_gaps()`, and it exists solely to synthesize a work-queue handoff brief:
a human staring at the dashboard sees zero epics, has no way to tell an epic needs attention
short of opening `docs/specs/epics/*.md` by hand, and `find_epic_gaps()` duplicates its own
epic-parsing (`_epic_status`, `_count_features`, `_citing_spec_ids`) that belongs in the
dashboard's own stage-detection alongside every other stage rule.

## What Changes

- Add a shared epic-stage detector to `dashboard.py`, following the same pure-file-inspection
  philosophy as `detect_stage()`: given an epic file under `docs/specs/epics/`, compute
  `epic-unparseable` (no `### Feature` headings — mirrors today's `unparseable: True` finding),
  `epic-complete` (terminal `**Status:**` line, or citing-spec count already covers every
  decomposed feature), or `epic-gap` (non-terminal status, fewer citing specs than features).
- Add a sibling scan entry point that lists every epic row for a `docs/specs/epics/` directory,
  mirroring `scan()`'s shape (`id`, `stage`, `next_action`, plus epic-specific fields: feature
  count, citing-spec count, citing spec ids).
- Wire epic rows into both the single-repo and multi-repo (`--repos`) code paths of `dashboard
  main()`'s JSON output, and into `render_dashboard()`'s human-readable text output, alongside
  (not merged into) the existing per-spec rows and sections.
- Refactor `seed_backlog.find_epic_gaps()` to call the new shared detector instead of
  re-implementing `_epic_status`/`_count_features`/`_citing_spec_ids` — its own seeded-brief
  behavior (seed keys, terminal-status skip, unparseable reporting, dedup) is unchanged.
- **Non-goal (explicitly out of scope):** auto-pick / work-claiming (`build_category_actions`,
  `build_category_items`, `auto_pick_brief`) is untouched — epic rows are visible, not claimable,
  in this change. Whether the auto-picker should be able to consume dashboard-native epic-gap
  rows directly (instead of only work-queue briefs) is a separate product/design decision, flagged
  for a human rather than decided here.

## Capabilities

### New Capabilities
- `epic-dashboard-visibility`: epics under `docs/specs/epics/` are detected and rendered as
  first-class dashboard rows (human text and JSON) with a computed lifecycle stage, using the
  same file-inspection approach the dashboard already documents for specs.

### Modified Capabilities
(none — `backlog-seeding`'s seeded-brief behavior for epics is unchanged; only its internal
epic-parsing implementation is deduplicated against the new shared detector, so no requirement
text in `openspec/specs/backlog-seeding/spec.md` changes.)

## Impact

- `src/worktrail/router/dashboard.py`: new epic-detection functions, a new `scan_epics()`-style
  entry point, `_NON_SPEC_DIRS` stays as-is for `scan()` (epics still aren't per-spec folders)
  but a new code path reads `docs/specs/epics/` directly, `main()` gains epic rows in both
  single-repo and `--repos` JSON payloads, `render_dashboard()` gains an epics section.
- `src/worktrail/workqueue/seed_backlog.py`: `find_epic_gaps()` delegates to the new dashboard
  detector; its own duplicated parsing helpers are removed.
- Tests: `tests/router/test_dashboard.py` (new epic-detection and rendering coverage),
  `tests/workqueue/test_seed_backlog.py` (existing epic-gap tests must keep passing unchanged
  against the refactored implementation).
- No change to CLI flags, work-queue brief schema, or auto-pick behavior.
