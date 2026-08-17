## Why

`ci-watch-loop.md`'s mandatory watch/finish commands (`gh pr checks --watch`,
`gh pr view --json state,mergedAt,mergeStateStatus,statusCheckRollup`,
`worktrail-check-review-threads`) all resolve through GitHub's GraphQL API,
with no documented REST alternative. A live GraphQL-specific outage makes
every one of those calls fail (observed directly on worktrail PR #500,
2026-08-17 — the same day the loop was hardened for the PR #498 stuck
check-run incident, a separate failure mode). An unattended drain/headless
worker hitting this has no path forward: it cannot even reach the loop's own
classification step to reclassify the failure, so it stalls indefinitely
instead of degrading gracefully or escalating cleanly.

## What Changes

- Add a REST-based fallback path to the loop's core waiting step ("Waiting
  for checks") and classification step ("When the checks settle, classify
  the results and act"), triggered when a `gh pr checks`/`gh pr view`
  GraphQL call fails with a GraphQL-outage signature (HTTP 503, or a GraphQL
  error body) rather than a normal check-pending/check-failing result.
- REST polling substitute for `gh pr checks --watch`: poll
  `gh api repos/$OWNER/$REPO_NAME/commits/$HEAD_SHA/check-runs` on an
  interval until every entry reaches a terminal `status`.
- REST substitute for the `gh pr view --json state,mergedAt,...` calls used
  in both the stuck-check-run fallback and case 1 ("All pass"):
  `gh api repos/$OWNER/$REPO_NAME/pulls/$PR_NUM` for `state`, `merged_at`,
  and `head.sha` (the REST equivalents of `state`, `mergedAt`, and
  `headRefOid`). Note which GraphQL-only fields (`mergeStateStatus`,
  `autoMergeRequest`, `statusCheckRollup`'s per-context detail) have no REST
  equivalent, and document the reduced-fidelity classification the loop
  falls back to while those fields are unavailable.
- Document how the loop resumes normal GraphQL-based operation once the
  outage clears, and how long it should keep retrying the REST fallback
  before treating the situation as an unrecoverable block.
- No code changes — `ci-watch-loop.md` is a procedural reference doc read
  directly by agents (via the `worktrail-go` skill), not a Python module;
  this mirrors PR #500's fallback addition to the same file, which also
  carried no spec deltas.

## Capabilities

### New Capabilities

(none — this is a documentation-only change to an existing agent-instruction
reference file, with no new or modified code-level requirement)

### Modified Capabilities

(none)

## Impact

- `skills/worktrail-go/references/ci-watch-loop.md` — the only file changed.
- No `src/worktrail/` code, no console scripts, no tests. `skip_specs: true`
  is set in this change's `.openspec.yaml` because no spec-level system
  behavior changes — only the documented agent procedure changes, matching
  the precedent set by PR #500 (`fix(go): add stuck check-run fallback to
  ci-watch-loop.md`), which touched the same file for a related purpose and
  carried no spec deltas.
- `tests/test_plugin_surface.py`'s cross-skill anchor citation check
  (`test_cross_skill_anchor_citations_resolve`) should still pass since no
  `{#anchors}` are removed or renamed, only added to.
