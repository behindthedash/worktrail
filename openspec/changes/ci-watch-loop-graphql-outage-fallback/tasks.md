## 1. "Waiting for checks" section

- [x] 1.1 Add a GraphQL-outage detection note directly under the existing
      `--watch` command block: a command that fails outright (non-zero exit
      with an HTTP 5xx or GraphQL-error-body in stderr) is a different
      failure mode from a `--watch` timeout with checks still pending, and
      routes to the new REST-fallback subsection below instead of the
      existing 3x-retry-then-stuck-check-run path.
- [x] 1.2 Add a new "GraphQL outage fallback" subsection (parallel in
      structure to the existing "Stuck check-run fallback" subsection):
      bounded discrete retries (3, matching the existing `--watch` retry
      cap) of `gh api repos/$OWNER/$REPO_NAME/commits/$HEAD_SHA/check-runs`,
      no hand-rolled sleep loop, citing this file's own existing rule
      against sleep loops (GO v1 defect L7) as the reason.
- [x] 1.3 Document how the loop returns to normal `--watch` operation on
      the next loop entry once GraphQL recovers (no persistent "degraded
      mode" flag to reset).
- [x] 1.4 Document the bounded-retries-exhausted path: falls through to the
      existing iteration-ceiling stop (case 5, `failed_recoverable`) with
      the outage noted as the cause, not a new terminal status.

## 2. Case 1 ("All pass") classification

- [x] 2.1 Add a REST substitute for the `gh pr view
      --json state,mergedAt,autoMergeRequest,headRefOid,mergeStateStatus,
      statusCheckRollup` call: `gh api repos/$OWNER/$REPO_NAME/pulls/$PR_NUM`,
      mapping `state`/`merged_at`/`head.sha`/`auto_merge` to the fields the
      existing branches already key off (`state == "MERGED"` becomes
      REST's `merged` boolean or `merged_at != null`; `headRefOid` becomes
      `head.sha`; `autoMergeRequest` becomes REST's `auto_merge` object).
- [x] 2.2 Document that `mergeStateStatus` and per-context
      `statusCheckRollup` history have no REST equivalent: when the REST
      substitute is active, the merge-state guard's CANCELLED/SUCCESS
      same-context pairing is skipped (treated as no signal, mirroring the
      review-thread gate's existing `checked: false` handling) rather than
      blocking completion.
- [x] 2.3 Document recording the reduced-fidelity guard in the eventual
      `--merge-result` text so a human reviewing `finish` output can see
      that the merge-state guard did not run for this completion.
- [x] 2.4 Confirm the stale-head guard and review-thread gate (which key
      off `$PUSH_SHA` and `worktrail-check-review-threads` respectively,
      not the fields this change adds a REST substitute for) are
      unaffected and need no wording changes beyond what 2.2/2.3 already
      cover.

## 3. Verification

- [ ] 3.1 [cleanup] Re-read the full edited `ci-watch-loop.md` end to end to
      confirm the new subsections read consistently with the existing
      "Stuck check-run fallback" subsection's structure and tone, and that
      no existing case numbering (1-5) or cross-references shifted. Tagged
      `[cleanup]` (tail kind): verification-only, no file scope of its own —
      needs 2.4's edits merged first.
- [ ] 3.2 [cleanup] Run `PYTHONPATH=src pytest -q -k test_plugin_surface` to
      confirm `test_cross_skill_anchor_citations_resolve` and the rest of
      the plugin-surface lockstep checks still pass (no anchors
      renamed/removed, only added). Tagged `[cleanup]` for the same reason
      as 3.1.
- [ ] 3.3 [cleanup] Run the full suite (`PYTHONPATH=src pytest -q &&
      PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`)
      to confirm this doc-only change has no unexpected side effects.
      Tagged `[cleanup]` for the same reason as 3.1.
