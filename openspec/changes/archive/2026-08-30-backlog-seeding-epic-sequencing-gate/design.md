## Context

`src/worktrail/router/dashboard.py` owns epic classification (`detect_epic_stage()`,
`scan_epics()`) and `src/worktrail/workqueue/seed_backlog.py`'s `find_epic_gaps()` delegates to
it, per the existing docstring: "so this module carries no duplicated status/feature-count/
citation parsing of its own." `detect_epic_stage()` currently returns one of three stages —
`epic-complete`, `epic-gap`, `epic-unparseable` — computed from a terminal `**Status:**` line,
an `### Feature` heading count, and a citing-spec count (`_epic_citing_spec_ids()`, matched via
`_epic_citation_patterns()`: literal epic id, `Epic NNN Feature M` prose, and documented
`**Future spec id:**` values). `seed_backlog.find_epic_gaps()` skips-and-reports
`epic-unparseable` findings instead of seeding them (see the `unparseable` branch and the
`unparseable_epics` summary list in `seed_backlog()`).

Separately, `dashboard.scan(repo / "docs" / "specs")` already returns one row per spec/change
folder (both `docs/specs/<id>/` and `openspec/changes/<id>/`, via `_openspec_change_dirs()`)
with an `id` and a `stage` computed by `detect_stage()`/`_safe_detect_openspec()`. `_ACTIVE`
(dashboard.py:2090) enumerates every stage that still needs work; `done` and `complete` are the
two stages a spec can reach that are *not* in `_ACTIVE` and represent finished task work (see
the `_CATEGORY_ORDER` comment: "`complete` = code done, open-PR -- terminal enough"). This is
the existing signal to reuse for "the gating feature's own task-completion state" rather than
inventing a second completion heuristic.

The real epic `docs/specs/epics/002-safe-work-queue-dependency-references.md` already contains
exactly this shape of prose today ("Feature 2 depends on Feature 1's contract", "Feature 3
depends on Feature 2's structured resolution states" under `## Dependencies`). Its Feature 2 and
Feature 3 `**Future spec id:**` values (`work-queue-conservative-dependency-resolution`,
`work-queue-dependency-diagnostics`) currently match live `openspec/changes/` folder names
1:1 — but Feature 1's (`work-queue-dependency-reference-contract`) does **not**: that feature
already shipped and its change folder was archived under a date-prefixed name
(`openspec/changes/archive/2026-08-18-epic-002-dependency-reference-contract/`), matched as a
citing spec only because its `proposal.md` *text* contains the future-spec-id string, not
because the folder is named after it. So "future spec id == literal folder name" holds only
for a still-open feature spec; resolution must handle the archived case too (Decision 4).

## Goals / Non-Goals

**Goals:**
- Detect an epic's own sequencing-gate prose for the specific feature the seeder is about to
  seed, using only text already on disk (no new epic-authoring convention required).
- Resolve a named gating feature to a real spec and reuse the existing dashboard stage as the
  completion signal.
- Fail safe: when gate resolution is inconclusive (gating feature not yet specced, unreadable,
  or in any non-`done`/`complete` stage), treat the gate as open and skip seeding, the same way
  `epic-unparseable` already skips rather than guesses.
- Leave every epic with no gate prose, and every already-closed gate, byte-for-byte unaffected.

**Non-Goals:**
- Per-feature citation mapping (which specific citing spec fulfills which specific feature
  number). The existing code already only tracks aggregate `cited`/`features` counts, not a
  feature→spec assignment; this change keeps that simplification and only adds gate detection
  for the *next* feature (`cited + 1`), consistent with how the brief focus text already talks
  about "the next unspecced feature."
- A generalized dependency-graph/DAG between features. Only the two prose shapes described below
  are recognized; anything else (e.g. "Feature 2 needs Feature 1 to be reviewed") is not a
  supported gate signal and is treated as no gate at all — a false negative here just means
  seeding proceeds as it does today, which is the pre-existing behavior, not a regression.
- Changing `needs-tasks`/`ready-to-implement` finders, `max_seeds`, or seed-key dedup semantics.

## Decisions

### 1. Gate detection lives in `detect_epic_stage()`, not in `seed_backlog.py`

Adding a fourth stage keeps epic classification single-owned by the dashboard module (per the
existing "delegates ... so this module carries no duplicated parsing" docstring), and lets the
dashboard's own rendering surfaces (which already special-case `epic-gap`/`epic-unparseable`,
e.g. `_CATEGORY_ORDER`-adjacent epic rendering around dashboard.py:2850) show a gated epic
distinctly from a genuinely-ready gap, instead of only `seed_backlog.py` knowing about it.

**Alternative considered:** do the gate check inline in `find_epic_gaps()` after calling
`scan_epics()`. Rejected — it would need its own copy of the feature-block/future-spec-id
parsing already owned by `dashboard.py`'s `_EPIC_FUTURE_SPEC_ID_RE`/`_epic_citation_patterns`,
duplicating exactly the parsing the module's own docstring says it avoids.

### 2. New stage name: `epic-sequencing-gated`

Distinct from `epic-gap` (unconditionally seedable) and `epic-unparseable` (no feature count at
all). `find_epic_gaps()` treats it like `epic-unparseable`: skip, don't seed, report via a new
`sequencing_gated_epics` list in `seed_backlog()`'s summary dict (parallel to the existing
`unparseable_epics` list), and log a line naming the epic, the blocked feature number, the
gating feature number, its resolved spec id (or "not yet specced"), and its stage.

### 3. Gate prose patterns (two, both scoped to the *next* feature number)

Computed against the full epic text (same text already read for `_count_epic_features`/
`_epic_status_header`), only after `features > 0` and `cited < features` (i.e. only when the
epic would otherwise be seeded as `epic-gap` — no need to parse gate prose for a fully-cited or
terminal epic).

Let `next_n = cited + 1`.

- **Pairwise**: `Feature\s+next_n\s+depends\s+on\s+Feature\s+(\d+)` (case-insensitive). Matches
  the epic 002 wording verbatim ("Feature 2 depends on Feature 1's contract").
- **Blanket**: `Feature\s+(\d+)\s+gates\s+(?:the\s+rest|the\s+remaining(?:\s+features?)?|later\s+
  features?|all\s+later\s+features?)` (case-insensitive), kept only when the captured feature
  number is `< next_n`. Matches the incident wording verbatim ("Feature 1 gates the rest").

Both patterns are checked; every match contributes a candidate gating feature number. This
mirrors `_epic_citation_patterns()`'s own style (a small, fixed list of compiled regexes, no
general NLP) and needs no change to how epic files are authored — both real examples in this
proposal (002's `## Dependencies` prose, and the incident's "gates the rest" prose) already read
naturally as one of these two shapes.

**Alternative considered:** a new structured per-feature marker (e.g. `**Sequencing gate:**`)
mirroring `**Future spec id:**`. Rejected for this change — it would require retrofitting every
existing epic's authoring convention before the detector could ever fire, where the two prose
patterns above already fire on real, currently-committed epic text with zero authoring changes.
Revisit only if the two prose patterns prove to have a real false-negative rate in practice.

### 4. Resolving a gating feature number to a spec, and to a completion signal

Split the epic text into per-`### Feature N` blocks (a new small helper alongside
`_count_epic_features`) and extract each block's own `**Future spec id:**` via the existing
`_EPIC_FUTURE_SPEC_ID_RE`, now applied per-block instead of globally. For a candidate gating
feature number `M`:

- No `### Feature M` block, or the block has no `**Future spec id:**` → gate is **open**
  (cannot resolve what "closed" would even mean; fail safe per Goals).
- Block resolves to future spec id `S` → build `S`'s own citation pattern (same shape
  `_epic_citation_patterns` already builds from a `**Future spec id:**` value) and run
  `_epic_citing_spec_ids(repo, [pattern])` to get every folder name whose content mentions `S`
  (reusing existing citation-matching infra rather than assuming the folder is literally named
  `S` — Feature 1's own archived case above proves that assumption is false in general). No
  match → gate **open** (not specced/cited yet). One or more matches → the gate is **closed** if
  *any* matched name resolves closed:
  - `(repo / "openspec" / "changes" / "archive" / name).is_dir()` → closed. This repo's own
    archive workflow (the `openspec-archive-change` skill: "Archive a completed change") only
    archives a change after it lands, so archived is a stronger completion signal than any
    dashboard stage.
  - Otherwise, look `name` up in the id→stage map built from `dashboard.scan(repo / "docs" /
    "specs")` (the same call `find_needs_tasks_specs`/`find_ready_specs` already make, so no new
    I/O pattern — it already covers both `docs/specs/<id>/` and live `openspec/changes/<id>/`).
    Present with `stage in {"done", "complete"}` → closed. Present with any other stage, or
    absent from the map entirely → not closed from this match (keep checking other matches, if
    any).
  - "Any match closed" rather than "every match closed": a broad citation pattern can also match
    unrelated prose that happens to name the same future spec id without being its actual
    implementation, so requiring *all* matches to resolve closed would let one irrelevant match
    hold the gate open forever; requiring *one real, closed match* is enough positive evidence
    the feature actually shipped.

If any candidate gating feature for `next_n` resolves **open**, the epic's stage is
`epic-sequencing-gated` and seeding is skipped. If all resolve **closed** (or no candidate gates
were found at all), stage is `epic-gap` exactly as today.

### 5. `cited + 1` as "the next unspecced feature" — accepted approximation

The gate check only needs to answer "is the feature the seeder is about to name in the brief
gated," and the existing brief-synthesis code already only ever names "the next unspecced
feature" in that generic sense (`_epic_brief_kwargs` never names a specific feature number
today). Using `cited + 1` keeps the same level of precision the seeder already operates at,
rather than introducing a stronger per-feature identity model this change doesn't otherwise need.

## Risks / Trade-offs

- **[Risk]** A pairwise/blanket regex match is coincidental prose, not an intended gate (e.g. a
  retrospective note reading "Feature 2 depends on Feature 1" about a different, already-shipped
  concern). → **Mitigation**: skip-and-report is the same fail-safe behavior `epic-unparseable`
  already uses; a false-positive gate match costs one skipped sweep and a log line, not a wrong
  brief. The epic's own `**Status:**` line remains the escape hatch (flip to terminal to stop
  seeding entirely), same as every other epic scenario.
- **[Risk]** A gating feature is specced under a different id than its documented
  `**Future spec id:**` (author deviated from convention). → **Mitigation**: resolves to gate
  **open** (spec not found under the expected id), which is the safe direction — worst case is
  one extra skipped sweep until the epic's `**Future spec id:**` is corrected or the spec is
  renamed to match, not a wrong brief.
- **[Trade-off]** No new epic-authoring convention is introduced, at the cost of only recognizing
  two specific prose shapes rather than an open-ended gate grammar. Consistent with the existing
  citation-pattern design (`_epic_citation_patterns`), which already accepts this same trade-off.

## Migration Plan

No data migration. `epic-sequencing-gated` is a new possible value of an existing `stage` field
that only appears for epics whose text already matches the new patterns — no existing epic file
in this repo (`001-managed-codex-runtime-validation.md`, `002-...md`) currently has a `cited <
features` gap combined with an open gate, so this ships with no immediate behavior change to
today's dashboard output; it only changes behavior the next time such an epic exists. Rollback is
a revert of the `dashboard.py`/`seed_backlog.py` diff — no persisted state to unwind, since a
skipped sweep creates no brief and no seed key.
