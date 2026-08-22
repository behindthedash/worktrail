## Why

`check_brief_staleness.py` bounds its history search using a brief's capture timestamp
(`original-created:`, falling back to `created:`), per the `stale-brief-precheck` spec's
"History Search Is Bounded By The Brief's Capture Time" requirement. For a brief that gets
released back to the queue and rechecked repeatedly over an extended period (e.g. brief
`20260623-093000-datalena-deferred-dep-upgrades`, 9 rechecks over 7 weeks), this boundary
never advances: every recheck re-searches from the brief's *original* capture time, so PRs
the brief's own prose already cites as done/superseded history (merged and resolved during a
prior recheck, weeks or months earlier) keep re-surfacing as "staleness evidence" on every
subsequent recheck. This forces an operator (or, in auto mode, a filed decision) to
re-adjudicate the same stale evidence every single recheck. Live incident: brief
`20260812-023701-check-brief-staleness-py-self`, observed in run `go-20260812-022551`
(2026-08-12), required an in-session operator override to proceed.

## What Changes

- `check_brief_staleness.py`'s CLI `_read_brief()` prefers a brief's `released-at:`
  frontmatter timestamp — already stamped by `work_queue.py release` on every recheck/release
  — as the `since` boundary passed to `check()`, when `released-at:` is present and parses.
  Falls back to the existing `original-created:` then `created:` precedence when
  `released-at:` is absent (a brief that has never been released back to the queue after a
  recheck).
- This makes a recheck search only for evidence that landed since the *last* recheck, not
  since the brief's original capture — the semantics a "recheck" implies, without changing
  `RACE_GRACE_SECONDS`, the grace-window computation itself, or CLI-flag probe extraction
  (unrelated prior work, PR #327/#396).
- The `stale-brief-precheck` spec's "History Search Is Bounded By The Brief's Capture Time"
  requirement documents the full `released-at:` > `original-created:` > `created:` precedence
  chain, with a new scenario covering a rechecked brief whose `released-at:` correctly
  excludes commits that landed (and were already accounted for) before that recheck.

## Capabilities

### Modified Capabilities
- `stale-brief-precheck`: the search-boundary requirement now documents reading
  `released-at:` in preference to `original-created:`/`created:` when a brief carries it (the
  rechecked-brief case).

## Impact

- `src/worktrail/router/check_brief_staleness.py`: `_read_brief()`.
- `openspec/specs/stale-brief-precheck/spec.md`: one requirement's text and scenario list.
- `tests/router/test_check_brief_staleness.py`: new coverage for the `released-at:`
  precedence and fallback behavior.
- No CLI flags, public function signatures beyond the `since` value's source field, or storage
  layout changes.
