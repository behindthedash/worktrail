## Why

`land_pr()` (`src/worktrail/router/land_pr.py:1826`) maps `mergeStateStatus == "BLOCKED"`
unconditionally to `final_status=blocked_product_decision` with merge_result
`"blocked on branch protection after merge-state guard and review-thread gate"`, and returns
`outcome="landed"`. Nothing in that branch asks *why* the merge state is blocked. GitHub reports
`BLOCKED` both for a real human/policy block (an unmet approval, a failing required check) and
for the purely mechanical "a required status check has not reported a conclusion yet" — which
the merge-state guard itself can cause, since it reruns CANCELLED/SUCCESS check pairs
(`_merge_state_guard`, `land_pr.py:1037`) and a freshly re-run check is pending, not concluded.
A head move on the PR (a rebase bot commit) resets the rollup the same way.

The consequence is the same class of failure `land-pr-wait-for-required-check-contexts`
(#1306, archived) closed one step earlier in the pipeline: a PR whose required CI has simply
not finished is reported as a **terminal, human-owned decision** and the run record is
*completed* as `blocked_product_decision`. Nothing retries it, and the queue reads it as done.
That earlier change gated only the CI *watch* on required-context coverage and explicitly did
not cover the merge-state guard; this is the gap it left open.

The set needed is already resolved in this same function and already imported:
`automerge_preflight.required_status_check_contexts()` is called at `land_pr.py:1596` and its
result is threaded into `_watch_ci`. It is in scope at the BLOCKED branch and unused there.

(Work-queue brief `20260920-152513-merge-state-guard-blocks-unreported`.)

## What Changes

- The BLOCKED branch consults the `statusCheckRollup` already present in the merge-state
  guard's payload and classifies `blocked_product_decision` only when every required status
  check context has reported a **terminal conclusion**.
- When a required context is missing from the rollup or is still pending, the pipeline re-polls
  the merge-state guard within a bounded budget instead of deciding. A merge state that leaves
  BLOCKED, or coverage that completes, resumes the existing flow unchanged.
- When that budget is spent with a required context still unreported, the outcome is `ceiling`
  / `failed_recoverable` — reconcilable — naming the outstanding contexts, not a completed
  `blocked_product_decision`.
- When the required-context read returned no answer (`None`) or the branch has zero required
  contexts (`[]`), behaviour is exactly what it is today: BLOCKED means
  `blocked_product_decision` immediately.

## Capabilities

### New Capabilities

### Modified Capabilities
- `land-pr-required-context-coverage`: extends required-context coverage from the CI watch to
  the merge-state guard's BLOCKED classification.

## Impact

- `src/worktrail/router/land_pr.py`: a new coverage helper over `statusCheckRollup`, a bounded
  re-poll around the BLOCKED branch, and the existing `required_contexts` value read there.
- `tests/router/`: new test module (kept out of `test_land_pr.py`, whose task chain already
  saturates the compile same-file gate).
- No CLI, `LandRequest`, or `LandOutcome` shape change. A repo with no ruleset-required
  contexts, or an unreadable ruleset, lands exactly as it does today.
