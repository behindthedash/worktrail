## Why

`seed_backlog.py`'s epic finder seeds an epic's next unspecced feature purely from a
features-vs-citing-spec count, with no awareness of an epic's own intra-epic sequencing gate
(e.g. an epic whose `## Dependencies` prose says "Feature 2 depends on Feature 1's contract," or
a feature whose own text says it "gates" later features until its evidence closes). This produced
a false-positive brief asking to spec Feature 2 of an epic while Feature 1's own governance-gate
spec had 0/16 tasks complete — planning work that a picking session cannot actually start yet,
presented as though it were ready.

## What Changes

- Add a sequencing-gate detector to the epic decomposition text: recognizes (a) pairwise
  "Feature N depends on Feature M" prose and (b) blanket "Feature M gates the rest / remaining /
  later features" prose, scoped to whichever gating feature(s) apply to the next unspecced
  feature (`cited + 1`).
- When a gate is found, resolve the gating feature's own spec via its documented
  `**Future spec id:**` and check that spec's own task-completion state (reusing the existing
  dashboard scan, not a new completion heuristic).
- If the gating feature's spec doesn't exist yet or its task-completion state is not closed
  (done/complete), the epic finder skips seeding that feature this sweep — mirroring the existing
  `epic-unparseable` skip-and-report pattern — rather than emitting a Route C brief that reads as
  ready planning work.
- Epics with no sequencing-gate prose, or whose named gate is already closed, are unaffected and
  seed exactly as they do today.

## Capabilities

### Modified Capabilities
- `backlog-seeding`: the "Epics with unspecced features are seeded by citation gap" requirement
  gains a sequencing-gate precondition — seeding the next unspecced feature is now also
  conditioned on any epic-declared gate for that feature being closed.

## Impact

- `src/worktrail/router/dashboard.py`: `detect_epic_stage()` gains sequencing-gate parsing and a
  new stage value for "gap exists, but gated"; `scan_epics()` output carries the new field(s).
- `src/worktrail/workqueue/seed_backlog.py`: `find_epic_gaps()` treats the new gated stage like
  `epic-unparseable` (skip + report); `seed_backlog()`'s summary dict gains a
  `sequencing_gated_epics` list alongside `unparseable_epics`.
- No change to the `needs-tasks`/`ready-to-implement` finders, `max_seeds`, or seed-key dedup —
  this only tightens the epic finder's own seed/skip decision.
- Test coverage: `tests/` mirrors `src/worktrail/router/dashboard.py` and
  `src/worktrail/workqueue/seed_backlog.py`, covering both the pairwise and blanket gate patterns,
  a closed gate (seeds normally), and epics with no gate prose (unaffected, regression coverage).
