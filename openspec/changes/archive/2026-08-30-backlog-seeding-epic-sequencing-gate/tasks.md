## 1. Dashboard: per-feature block and gate-prose parsing

- [x] 1.1 Add a helper that splits epic text into per-`### Feature N` blocks (heading number ->
      block text), reusing `_EPIC_FEATURE_HEADING_RE`'s heading detection but keyed by feature
      number instead of a bare count.
- [x] 1.2 Add a helper that extracts a block's `**Future spec id:**` value via the existing
      `_EPIC_FUTURE_SPEC_ID_RE`, applied to one feature block instead of the whole document.
- [x] 1.3 Add the pairwise gate regex (`Feature\s+<next_n>\s+depends\s+on\s+Feature\s+(\d+)`) and
      the blanket gate regex (`Feature\s+(\d+)\s+gates\s+(?:the\s+rest|the\s+remaining(?:\s+
      features?)?|later\s+features?|all\s+later\s+features?)`), both case-insensitive, per
      design.md Decision 3.
- [x] 1.4 Add a helper that, given `next_n` and the full epic text, returns the set of candidate
      gating feature numbers from both patterns (blanket matches filtered to `< next_n`).

## 2. Dashboard: gate resolution and new stage

- [x] 2.1 Add a helper that resolves one candidate gating feature number `M` to closed/open per
      design.md Decision 4: look up `M`'s block's future spec id; if absent, open; else build its
      citation pattern (mirroring `_epic_citation_patterns`'s future-spec-id branch) and call
      `_epic_citing_spec_ids(repo, [pattern])`; open if no matches; else closed iff any matched
      name resolves closed (archived under `openspec/changes/archive/<name>/`, or present in a
      `dashboard.scan(repo / "docs" / "specs")` id->stage map with stage in `{"done",
      "complete"}`).
- [x] 2.2 In `detect_epic_stage()`, after computing `features`/`cited` and confirming
      `cited < features` (today's `epic-gap` branch), compute `next_n = cited + 1`, find
      candidate gates for `next_n`, resolve each, and return the new `epic-sequencing-gated`
      stage (with the blocked feature number, and per-gate `{feature, spec_id, resolved_name,
      closed}` detail for logging) if any resolves open; otherwise fall through to today's
      `epic-gap` result unchanged.
- [x] 2.3 Confirm `scan_epics()` needs no changes beyond returning whatever
      `detect_epic_stage()` now returns (it already forwards the dict as-is).

## 3. Seeder: skip-and-report the new stage

- [x] 3.1 In `find_epic_gaps()`, add an `epic-sequencing-gated` branch alongside the existing
      `epic-unparseable` branch: do not add a seedable candidate; append the finding (repo, epic
      id, blocked feature number, gate detail) to a new local list, mirroring how `unparseable`
      findings are collected today.
- [x] 3.2 In `seed_backlog()`, log one line per sequencing-gated finding (epic id, blocked
      feature number, gating feature number(s), resolved spec id or "not yet specced", and
      stage), matching the existing unparseable-epic log line's style.
- [x] 3.3 Add a `sequencing_gated_epics` key to `seed_backlog()`'s returned summary dict,
      parallel to `unparseable_epics` (list of `{"repo": ..., "id": ...}`).

## 4. Tests: dashboard gate detection

- [x] 4.1 In `tests/router/test_dashboard.py`, add a case: epic with pairwise gate prose
      ("Feature 2 depends on Feature 1's contract") whose Feature 1 future-spec-id has no citing
      spec at all -> `detect_epic_stage()` returns `epic-sequencing-gated`.
- [x] 4.2 Add a case: same epic, but Feature 1's future spec id is cited by a spec/change folder
      whose `dashboard.scan()` stage is `ready-to-implement` (the 0/16-tasks incident shape) ->
      still `epic-sequencing-gated`.
- [x] 4.3 Add a case: same epic, Feature 1's citing spec/change folder's stage is `done` ->
      `epic-gap` (seedable), unchanged from today's behavior.
- [x] 4.4 Add a case: Feature 1's citing folder exists only under `openspec/changes/archive/
      <date-prefixed-name>/` (content-matches the future spec id, folder name does not) ->
      treated as closed -> `epic-gap`. Mirrors the real archived-Feature-1 shape found in
      `docs/specs/epics/002-safe-work-queue-dependency-references.md` during design.
- [x] 4.5 Add a case: blanket gate prose ("Feature 1 gates the rest") with an open Feature 1 ->
      `epic-sequencing-gated`, and confirm it also applies when the next unspecced feature is
      Feature 3, not just Feature 2 (blanket gate covers every later feature).
- [x] 4.6 Add a case: gate prose names a feature other than `next_n` (e.g. "Feature 3 depends on
      Feature 2" while the next unspecced feature is Feature 2 itself) -> no gate applies ->
      `epic-gap`, unchanged.
- [x] 4.7 Add a regression case: an epic with no gate prose at all (e.g. shaped like
      `001-managed-codex-runtime-validation.md`) -> stage and fields identical to pre-change
      behavior.
- [x] 4.8 [e2e] Run existing `tests/router/test_dashboard.py` epic-stage tests to confirm no
      existing assertions on `epic-gap`/`epic-complete`/`epic-unparseable` regressed.

## 5. Tests: seeder skip-and-report

- [x] 5.1 In `tests/workqueue/test_seed_backlog.py`, add a case: an epic in
      `epic-sequencing-gated` stage produces no queued brief and no `seed_key` collision with a
      later sweep.
- [x] 5.2 Add a case: `seed_backlog()`'s returned summary includes the epic under
      `sequencing_gated_epics`.
- [x] 5.3 Add a case: the sweep's log output includes a line naming the epic, the blocked feature
      number, and the gate's resolved state.
- [x] 5.4 Add a regression case: an epic already covered by an existing `find_epic_gaps`/
      `seed_backlog` test (no gate prose) still seeds exactly as before.

## 6. Verification

- [x] 6.1 [e2e] Run `PYTHONPATH=src pytest -q` (full suite).
- [x] 6.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` (golden
      record/replay regression, per AGENTS.md).
- [x] 6.3 [e2e] Manually sanity-check `docs/specs/epics/002-safe-work-queue-dependency-references.md`
      against `detect_epic_stage()` (all three features now closed/archived) still reports
      `epic-complete`, not a new gated/gap stage — confirms the design's "no existing epic
      changes behavior" claim.
