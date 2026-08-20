## Why

`find_epic_gaps()`'s `_citing_spec_ids()` counts a spec/change folder as citing an epic only
when the literal epic id string (e.g. `002-safe-work-queue-dependency-references`) appears
verbatim in one of that folder's top-level markdown files. Real citations frequently reference
the epic by prose instead — an OpenSpec change's `proposal.md` typically reads "Epic 002
Feature 2 adds..." rather than spelling out the full epic id (live example:
`openspec/changes/work-queue-conservative-dependency-resolution/proposal.md`, which cites
"Epic 002 Feature 2" twice and never the literal id string). This undercounts real citations
and produces a misleading "only N spec(s) cite it" seeded brief even after the cited feature
is fully specced, implemented, and merged — observed live: brief
`20260819-021834-epic-002-safe-work-queue` claimed "only 1 spec cites it" for epic 002 the day
after Feature 2 (`work-queue-conservative-dependency-resolution`) merged. This directly
violates the `backlog-seeding` spec's own "Every decomposed feature has a citing spec" scenario
for that requirement.

## What Changes

- `_citing_spec_ids()` also counts a spec/change folder as citing the epic when its markdown
  matches either of two additional signals: `Epic <NNN> Feature <M>` prose (`<NNN>` derived
  from the epic id's leading three-digit number), or any of the epic's own documented
  `**Future spec id:**` values (extracted from the epic markdown).
- The literal epic id string match is preserved unchanged as the first, strongest signal — this
  is additive, not a replacement.
- Audited the other epic-decomposition briefs the reporting brief named
  (`worktrail:epic:001-managed-codex-runtime-validation`,
  `datalena:epic:002-feedback-capture-integration`) for the same false-stale signal: neither is
  affected. Epic 001 genuinely has zero citations by any signal (no follow-up needed here).
  Datalena epic 002's two queue entries (`cited=1` then `cited=2`) are the spec's own documented
  progress-keyed re-arm behavior as citations genuinely increased over time, not a duplicate-bug
  artifact.

## Capabilities

### Modified Capabilities

- `backlog-seeding`: the "Epics with unspecced features are seeded by citation gap" requirement
  now documents matching a spec/change folder against the epic id, `Epic <NNN> Feature <M>`
  prose, or a documented future spec id — not the literal epic id string alone.

## Impact

- `src/worktrail/workqueue/seed_backlog.py`: `_citing_spec_ids()` (now takes precompiled
  citation patterns instead of a bare epic id string) and a new `_epic_citation_patterns()`
  helper; `find_epic_gaps()`'s single call site.
- `openspec/specs/backlog-seeding/spec.md`: one requirement's text and scenario list.
- `tests/workqueue/test_seed_backlog.py`: new coverage for prose and future-spec-id citation
  signals; `_mk_epic()` gains an optional `future_spec_ids` fixture parameter.
- No CLI flags, seed-key format, or `find_epic_gaps()` public return shape changes.
