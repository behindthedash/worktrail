# Investigation: does the dependency-freshness precondition fire in practice?

Brief: `20260910-160812-verify-dependency-freshness-precondition-fires`.

## Verified Observations

- PR #1132 (feature: `dependency_freshness.py`, `premise_check.py` integration,
  `queue_triage.py` prompt-block rendering) merged to `main` at
  `2026-09-10T22:55:48Z`. PR #1134 (checkbox-sync + archive of
  `openspec/changes/archive/2026-09-10-triage-dependency-freshness-precondition/`)
  merged at `2026-09-10T23:06:44Z`. Both confirmed via `gh pr view`.
- One live evaluate run has occurred since the merge: the direct single-brief
  triage (`worktrail-skill-dispatch --evaluate-brief-triage`, tagged
  `seeded-from: triage:2026-09-10:direct`) that produced this very brief's own
  `## Triage 2026-09-10` note. That run rendered a populated "Dependency
  freshness" block with status `stale` for `src/worktrail/.fixtures/sample-spec`
  (typescript/vitest locked but not installed) — one confirmed in-practice fire.
- No **scheduled** queue_triage evaluate run has occurred since the merge:
  - The nightly drain's bulk `worktrail-queue-triage evaluate` last ran at
    `2026-09-10T09:17:02Z` (`~/.worktrail/triage/drain-20260910T091702Z/verdict.json`,
    18 entries), over 13 hours **before** the merge. The next nightly run is
    `~2026-09-11T09:17:02Z`.
  - `worktrail-queue-triage evaluate --help` states its recommended cadence is
    "monthly, or pre-drain weekly — not nightly" for the full-queue bulk form;
    the per-brief direct form (used above) runs on-demand only, whenever a
    human or `/go` triages an untriaged intake brief directly.
- The `Verdict`/`VerdictEntry` dataclass persisted to each run's `verdict.json`
  (`src/worktrail/workqueue/queue_triage.py:1354`) has **no** `dependency_freshness`
  field — confirmed by reading the dataclass definition and by inspecting a
  verdict entry from `drain-20260910T091702Z/verdict.json`. The freshness
  check's result is rendered into the evaluator's prompt (`queue_triage.py:149`,
  `:1314`) but never written back into any persisted artifact. The only trace
  of it surviving a run is an evaluator's own free-text mention inside a
  brief's `## Triage` note, which happens only when the evaluator judges it
  relevant to that brief's own focus (as it did for this brief, because this
  brief's focus *is* the feature).

## Unknowns / Missing Evidence

- Whether the block renders correctly for repos other than worktrail's own
  `.fixtures/sample-spec` (e.g. datalena, gracefully-giving-back — real
  npm-based repos with their own lockfile drift).
- Whether the D3 keep-bias rule specifically is exercised by a `stale`/`unknown`
  freshness result on a *different* brief (this one's own `keep` verdict was
  driven by "observation over future runs," not by a freshness-triggered
  keep-bias) — no second data point exists yet.
- Sustained behavior across "several" runs, as the brief asks: fundamentally
  can't be answered yet — under one hour has elapsed since merge, and the only
  two ways this fires (a nightly drain run, or another direct single-brief
  triage) have not recurred yet.

## Hypotheses

- **Hypothesis:** the mechanism will keep firing correctly on future runs,
  since it is unit-tested (`tests/workqueue/test_dependency_freshness.py`,
  `test_premise_check.py`, `test_queue_triage.py`, all merged in PR #1132) and
  fired correctly on its first live exercise. Not yet confirmed by a second
  independent observation.

## Validation Steps

- After the next nightly drain run (`~2026-09-11T09:17:02Z`), check
  `~/.worktrail/drain-logs/<timestamp>.json` and the corresponding
  `~/.worktrail/triage/drain-<timestamp>/verdict.json` for briefs against
  npm-based repos; since the freshness block itself isn't persisted, this
  requires either instrumenting `queue_triage.py` to log the block per group
  (out of scope here — Route I permits diagnostics, not a feature change) or
  reading whichever briefs' own `## Triage` notes happen to mention it.
- Re-run `worktrail-check-resumable-state`-style evidence gathering after 2-3
  more nightly drain cycles have passed, or after the next monthly/pre-drain
  full `worktrail-queue-triage evaluate` run.

## Confirmed Root Cause

Not applicable — this is a verification task, not a defect investigation.
The feature is confirmed present and exercised once; sustained behavior across
several runs is not yet provable given the elapsed time since merge.

## Recommended Next Route

None — re-queue the same brief for a later recheck (`release
--next-check-after`) rather than opening new work. Recheck after
`2026-09-17` (one week), by which point at least 6 nightly drain cycles will
have run.
