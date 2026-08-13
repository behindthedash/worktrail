## Why

`Verifier.verify_one` (`src/worktrail/orchestrator/verify.py:1300`) quarantines a
group the instant `ensure_mergeable` / `wait_and_fix_ci` / `resolve_review_threads`
/ `_merge_with_cumulative_gate` exhausts this run's own bounded poll/strike budget
and returns `ok=False` — it never rechecks the PR's actual live state before
writing `quarantined[name] = reason` (`verify.py:1372-1388`). A repo's own external
auto-merge automation (`go-policy.yaml` `external_automerge`, e.g.
`.github/workflows/auto-merge.yml`) keeps acting on the PR independently of this
run's poll loop, and can land the merge moments after this run's budget already
gave up. This was observed live 2026-08-12/13 on worktrail PR #339 (run
`go-20260812-161537`): the 3rd ci-fix worker applied `go:no-version-bump`, CI went
green, `github-actions` auto-merge landed the PR at 02:10, but `verify_one` had
already recorded `QUARANTINED` moments earlier — the journal had to be
hand-reconciled from `QUARANTINED` to `MERGED` before the run could proceed to its
tail tasks. A merged PR incorrectly recorded as quarantined leaves a stale
worktree, blocks dependent groups from seeing their base as merged, and requires
manual journal surgery to unstick a run that actually succeeded.

## What Changes

- In `verify_one`, immediately before finalizing an *ordinary* `QUARANTINED`
  verdict (i.e. `not ok`, and neither a self-merge violation nor a post-merge
  regression — those two cases already have their own distinct, correct
  handling), add one passive, last-chance recheck of the PR's live state:
  - Call the existing `pr_status(gb)` primitive. If `state` is already `MERGED`,
    record the group as `MERGED` (append to `merged`, run the normal
    `cleanup_group` path) instead of `QUARANTINED`.
  - Else, if the PR shows a live `autoMergeRequest` (armed by the repo's own
    external automation, not by this run), give it exactly one bounded wait via
    the existing `_wait_for_external_merge` helper before finalizing
    `QUARANTINED`. If that wait confirms `MERGED`, record `MERGED`; otherwise
    finalize `QUARANTINED` with the original failure reason.
  - This recheck is strictly passive: it never calls `gh pr merge`, never arms
    auto-merge, and never mutates the PR. It only reuses `pr_status` and
    `_wait_for_external_merge`, both already exercised elsewhere in this file
    (`auto_merge`, `_wait_for_external_merge`'s existing call site at
    `verify.py:1086`) — no new polling machinery.
  - Self-merge violations (`self_merged`) and post-merge regressions
    (`post_merge_regressed`) are unaffected: those verdicts are already correct
    given a confirmed merge and must not be reinterpreted by this recheck.
- Add regression test coverage in `tests/orchestrator/test_verify.py` (or the
  correct existing test module for `Verifier.verify_one`) for the exact race:
  a group whose bounded checks report `not ok`, but whose live `pr_status` shows
  `MERGED` (and separately, shows an armed `autoMergeRequest` that then merges
  within the bounded wait) ends up in `merged`, not `quarantined`.

## Capabilities

### New Capabilities
- `quarantine-live-merge-recheck`: `verify_one`'s last-chance passive recheck of
  a PR's live merge state before finalizing an ordinary quarantine verdict.

### Modified Capabilities
(none — no existing spec capability currently documents `verify_one`'s
quarantine-finalization behavior)

## Impact

- **Code**: `src/worktrail/orchestrator/verify.py` — `Verifier.verify_one` only.
  No changes to `ensure_mergeable`, `wait_and_fix_ci`, `resolve_review_threads`,
  `_merge_with_cumulative_gate`, `pr_status`, or `_wait_for_external_merge`
  themselves; this change is purely a new call site reusing them.
- **Tests**: new regression coverage in the orchestrator test suite for
  `verify_one`'s quarantine-vs-live-merge race.
- **Behavior**: a group that would previously land in `quarantined` purely
  because this run's own poll/strike budget expired — while the PR was in fact
  already merged, or merges within one bounded external-merge wait — now lands
  in `merged` instead. Self-merge-violation and post-merge-regression verdicts
  are unchanged. No change to `ensure_mergeable`/`wait_and_fix_ci`/
  `resolve_review_threads`/`_merge_with_cumulative_gate` budgets themselves, and
  no new autonomous merge-arming behavior is introduced.
