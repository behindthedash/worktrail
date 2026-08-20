## Context

`src/worktrail/router/dashboard.py` is pure file inspection: `scan()` walks `docs/specs/*/`,
`detect_stage()` classifies one spec folder, `scan_repos()` fans that out across repos, and
`main()`/`render_dashboard()` project the result into JSON and human text. `_NON_SPEC_DIRS`
(including `"epics"`) exists specifically so loose `.md` files under those directories are never
mistaken for a spec doc by `_is_spec_folder()` — that exclusion is correct and stays.

Today `docs/specs/epics/` is read by exactly one other module: `seed_backlog.find_epic_gaps()`,
which parses `**Status:**`, counts `### Feature` headings, builds citation-match patterns, and
scans `docs/specs/` + `openspec/{specs,changes}` for citing folders — purely to decide whether to
seed a work-queue brief. `seed_backlog.py` already imports from `..router import dashboard`, so
the dependency direction (`workqueue` → `router`) is established; moving the epic-parsing logic
into `dashboard.py` and having `seed_backlog` call it back is a refactor in the existing
direction, not a new circular dependency.

See proposal.md for the motivating gap (epics invisible to the dashboard) and non-goal (auto-pick
untouched).

## Goals / Non-Goals

**Goals:**
- One shared epic-stage detector, used by both the dashboard's own rendering and
  `seed_backlog.find_epic_gaps()`, so the two can never silently disagree about what counts as a
  citation or a terminal status.
- Epic rows visible in both JSON and human dashboard output, single-repo and multi-repo, with
  zero change to output for repos that have no epics backlog (empty-diff safety for the common
  case).
- `find_epic_gaps()`'s external behavior (seed keys, dedup, unparseable reporting, terminal-status
  skip) is byte-for-byte unchanged — this is an internal refactor of its epic-parsing, not a
  behavior change to backlog seeding.

**Non-Goals:**
- Changing what `scan()` itself returns for `docs/specs/*` spec folders, or touching
  `_NON_SPEC_DIRS`/`_is_spec_folder()`. Epics are a distinct row kind added alongside spec rows,
  not merged into `scan()`'s existing list.
- Auto-pick / claiming (`build_category_actions`, `build_category_items`, `auto_pick_brief`,
  the work queue). Epic rows are read-only visibility.
- A new CLI flag to opt in/out of epic rows — visibility is unconditional, matching how spec rows
  are unconditional today.

## Decisions

**Epic rows are a sibling scan, not merged into `scan()`'s return list.** `scan()`'s return value
is consumed by `find_needs_tasks_specs()`, `find_ready_specs()`, and `drain.py`, all of which
filter by spec-shaped stage values (`needs-tasks`, `ready-to-implement`, ...) and assume every row
came from a spec folder (e.g. task-count fields). Even though the three new epic stage values
(`epic-gap`, `epic-unparseable`, `epic-complete`) wouldn't collide with any existing stage filter,
merging heterogeneous row shapes into one list is a correctness trap for any future `scan()`
consumer that iterates rows assuming a spec shape. A separate `scan_epics()`-shaped function
(mirroring how `scan_repos()` already composes `scan()` rather than replacing it) keeps the two
row kinds structurally distinct while still letting `main()`/`render_dashboard()` display them
together. This matches the proposal's explicit allowance: "Extend `dashboard.scan()` (or a
sibling function)".

**The shared detector lives in `dashboard.py`; `seed_backlog.py` calls it, not the reverse.**
`seed_backlog.py` already depends on `router.dashboard` (`find_needs_tasks_specs` calls
`dashboard.scan`). Putting the epic detector in `dashboard.py` keeps that single dependency
direction — `seed_backlog.find_epic_gaps()` becomes a thin wrapper that calls the shared
per-epic detector for each file in `docs/specs/epics/` and adapts the result into its own
brief-seeding shape (`seed_key`, `citing_specs` list, etc.), rather than two independent
implementations of `**Status:**`/`### Feature`/citation parsing drifting apart over time.

**Stage values are `epic-gap` / `epic-unparseable` / `epic-complete`**, exactly as named in the
task description, chosen to read unambiguously next to the existing spec stage vocabulary
(`needs-tasks`, `ready-to-implement`, ...) without colliding with any of it.

**Terminal-status epics classify as `epic-complete`, not a fourth stage.** `find_epic_gaps()`
today treats a terminal `**Status:**` line as "nothing left to spec" (skip), identically to a
fully-cited epic. Reusing `epic-complete` for both keeps the stage vocabulary at three values
instead of inventing a distinction (`epic-terminal` vs `epic-cited-complete`) the current code
doesn't make and no consumer needs.

**Malformed epic files degrade per-row, matching `_safe_detect_stage`/`_safe_detect_openspec`.**
The dashboard's existing philosophy is that one broken folder must never crash the whole render;
the epic scan follows the same per-item try/except-to-error-row pattern already established for
spec folders and OpenSpec changes.

## Risks / Trade-offs

- **Human-render section placement** — adding a new section to `render_dashboard()`'s text output
  changes the rendered string for any repo that *does* have outstanding epics (previously
  invisible, now shown). This is the change's entire point, but any downstream code snapshotting
  the rendered text (e.g. a test fixture) needs updating. Mitigation: keep the new section
  additive at the end of the existing section list, and confirm via `tests/router/test_dashboard.py`
  that repos with zero epics produce byte-identical output to before this change.
- **Refactor risk to `find_epic_gaps()`** — moving its citation/status/feature-count logic into
  `dashboard.py` risks subtly changing behavior the existing `tests/workqueue/test_seed_backlog.py`
  suite already pins down. Mitigation: that suite is the acceptance gate for the refactor; it
  must pass unmodified.

