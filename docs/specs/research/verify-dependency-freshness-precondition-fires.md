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
- **Corrected 2026-09-17 (see Update below): this bullet was wrong.** The
  `Verdict` dataclass *does* persist a `dependency_freshness` field
  (`queue_triage.py:1442`), added in the same PR #1132 commit this note
  already cites. The `drain-20260910T091702Z/verdict.json` entries inspected
  at the time had no such field only because that nightly run executed
  *before* the merge (13 hours earlier, per the bullet above it) — not
  because the field didn't exist in code. Left as originally written below
  for the record; do not rely on it.

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

## Update 2026-09-17 (one week later)

Re-checked `~/.worktrail/triage/drain-*/verdict.json` for every nightly drain
cycle between the 2026-09-10 merge and today (dirs exist for 09-11, 09-12,
09-15, 09-16; 09-13/09-14/09-17 have no `drain-*` directory, i.e. those
nightly runs either didn't reach the bulk `evaluate` step or ran before this
check — not investigated further, out of scope for this brief). Findings
supersede the "Verified Observations" bullet above about the dataclass having
no `dependency_freshness` field — that bullet was **wrong**, confirmed by
re-reading the same commit (`072ef816`, PR #1132) that both bullets cite: the
field (`Verdict.dependency_freshness`, `queue_triage.py:1442`) and its
persistence into the group-level `dependency_freshness` key
(`queue_triage.py:1733`/`1795`) were added in that same commit, not later.

- **Persistence confirmed across 4 separate nightly runs** (09-11, 09-12,
  09-15, 09-16 — 17-35 entries each): every `verdict.json` entry from these
  runs carries a `dependency_freshness` field, non-empty for the large
  majority of briefs. Verified by direct inspection of the JSON files, not
  by re-running anything.
- **Populated with both real statuses**, matching the design: `stale` for
  most repos with lockfile/`node_modules` drift (datalena, gracefully-giving-back,
  career-teleprompt, when-truth-becomes-optional, worktrail's own
  `.fixtures/sample-spec`), and `fresh` for two observed cases (aspens,
  wake-up-sooner) where installed packages matched the lockfile exactly. This
  is the "several runs" evidence the brief asked for — the precondition fires
  reliably in production, not just in the one run observed a week ago.
- **The specific npm-test-skip behavior (D2) has still not been observed
  firing.** Searched every `premise_check` entry across all 4 runs for a
  `command`-kind needle shaped like `npm test`/`vitest` coincident with a
  `stale`/`unknown` root: none exists. The only `command`-kind premise
  checks present in this window (`npm`, `pip`, two `worktrail-work-queue
  link` needles) were all rejected by the *separate*, pre-existing
  command-allow-list gate (`detail: "command not allow-listed; not run"`),
  never reaching the freshness-skip branch at all. No brief in this window
  happened to need a real npm-test-shaped reproduction against a stale root.
- **The D3 keep-bias rule's behavioral effect on the evaluator is still
  unconfirmed.** Searched every `judgment_reason`/`evidence` field across all
  4 runs' verdicts for language matching the design's expected keep-bias
  phrasing ("stale root", "mismatched packages", "dependency freshness"):
  no match. Also checked every `report.md` for the same run set: zero
  mentions of "Dependency freshness" anywhere. The block is being computed
  and rendered into the evaluator's prompt on every run (confirmed above),
  but nothing in the persisted artifacts shows the evaluator's reasoning
  actually being shaped by it — plausible explanation (**Hypothesis**, not
  confirmed): no brief in this window presented a scenario where a `stale`
  root's tooling output was the *only* candidate reproduction evidence, so
  the rule's condition (prefer `keep` when reproduction would rely on
  running stale-root tooling) never had a case to visibly act on.

## Confirmed Root Cause

Not applicable — this is a verification task, not a defect investigation.
The freshness-check-and-render mechanism (D1/D2 prompt block, D4 persistence)
is now confirmed firing reliably across a full week of nightly runs. The two
narrower behaviors the brief specifically asks about — the npm-test-skip
firing in a live case (D2) and the evaluator visibly applying the D3
keep-bias rule — remain unconfirmed, not because they're broken, but because
no nightly run in this window produced a brief whose premise reproduction
depended on running a stale root's own tooling.

## Recommended Next Route

None — re-queue the same brief for a later recheck (`release
--next-check-after`) rather than opening new work. The residual evidence gap
(D2 skip + D3 behavioral keep-bias) is narrow and coincidence-dependent
rather than schedule-dependent, so a fixed recheck date is a weaker signal
than before; recheck after `2026-10-01` (two more weeks) and, if the gap
persists, consider narrowing the brief's ask to just those two behaviors
since the "does it fire at all" question this brief opened with is now
answered yes.
