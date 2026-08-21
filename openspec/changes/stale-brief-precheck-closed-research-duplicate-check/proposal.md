## Why

`check_brief_staleness.py`'s forward-looking search only catches work that lands *after* a
brief's own `created:` timestamp; `cluster_detect.py`'s duplicate-brief clustering only
compares briefs simultaneously present in `queue/`. Neither catches a fresh brief that
independently re-investigates ground an *earlier, already-claimed-and-resolved* brief already
covered — once that earlier brief has moved to `picked/` with `status: done`, it is invisible to
both checks. Reproduced live 2026-08-21: brief `20260821-105129` re-investigated the exact
worktrail run-record lifecycle gap that brief `20260821-084314-bridge-health-guard-invariant-failing`
had already investigated and closed 68 minutes earlier via PR #590 (commit `2151f13`), which
published `docs/specs/research/dead-dispatch-backlog-investigation.md`. Both existing checks ran
clean, because the staleness search boundary is anchored to the *new* brief's own `created:`
(which postdates the PR that resolved the topic) and cluster-detect never looks past `queue/`.

## What Changes

- `check_brief_staleness.py`'s `check()` gains a second, independent, backward-looking search:
  given the same extracted probes (`extract_probes()`, unchanged) it already computes for the
  forward-looking git/PR search, it also scans `docs/specs/research/*.md` notes touched on the
  base branch within a bounded, documented lookback window anchored to the brief's own capture
  time, and reports literal probe-text overlap against each candidate note's content as a new
  `research_notes` result field — independently degradable (its own warning), never affecting
  `checked` or the existing `matches`/`pull_requests` fields.
- The existing forward-looking git/PR search (`matches`, `pull_requests`) is completely
  unchanged; this is a pure addition alongside it, sharing the same probe extraction and the
  same fail-open contract (never raises, `checked: false` on any I/O/parse failure, evidence
  never auto-applied).
- `skills/worktrail-go/references/brief-staleness-check.md`'s Phase 5.5 branch is updated so a
  non-empty `research_notes` result triggers the same "File-state verification" step and the
  same operator prompt as an existing `matches`/`pull_requests` result — one ask site, not a
  second separate one — and its JSON example / result table document the new field.
- `openspec/specs/stale-brief-precheck/spec.md` gains a new requirement documenting the
  backward-looking research-note search: its lookback window, its bounding (note-count cap,
  subprocess timeout), and that it is surfaced through the existing evidence contract.

## Capabilities

### Modified Capabilities
- `stale-brief-precheck`: adds a new requirement for the backward-looking research-note search
  (a new, additional evidence source alongside the existing forward-looking commit/PR search;
  no existing requirement's behavior changes).

## Impact

- `src/worktrail/router/check_brief_staleness.py`: new `research_notes` search inside `check()`,
  new module-level constants for its lookback window/caps, `_format_human()` and the CLI JSON
  output extended to include `research_notes`.
- `skills/worktrail-go/references/brief-staleness-check.md`: "Reading the result" table, JSON
  example, and operator-prompt wording extended to cover `research_notes`.
- `openspec/specs/stale-brief-precheck/spec.md`: one new requirement (no existing requirement
  text changes).
- `tests/router/test_check_brief_staleness.py`: new coverage for the research-note search
  (window boundaries, cap behavior, fail-open degradation, `_format_human()`/CLI JSON shape).
- No CLI flag or public-function signature changes beyond `check()`'s return-dict shape gaining
  one new key; no storage-layout changes.
