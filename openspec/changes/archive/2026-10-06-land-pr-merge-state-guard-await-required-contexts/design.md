## Context

`land_pr()` reaches the BLOCKED branch only after `_watch_ci` returned `settled` and the
review-thread gate passed. Since #1306 the watch already refuses to settle without
required-context coverage, so the interesting question is how a *covered* watch can still be
followed by a BLOCKED merge state with an unreported required context:

- `_merge_state_guard` reruns CANCELLED checks that have a SUCCESS twin (up to
  `MERGE_STATE_RERUN_MAX`). The rerun it just triggered is pending by construction, and the
  guard's own final `gh pr view` can observe it that way.
- The PR head can move between the watch and the guard (a rebase bot commit), resetting the
  rollup to pending.
- `required_contexts` may have been `None` at watch time (a transient ruleset read failure) and
  readable later — the resolution happens once, so the watch's fallback does not bind here.

The payload is already in hand: `_merge_state_guard` requests `statusCheckRollup` alongside
`mergeStateStatus`, and each entry carries `conclusion` (checks) or `state` (statuses).

## Goals / Non-Goals

- Goal: never *complete* a run record as `blocked_product_decision` on a merge state that is
  blocked only because a required check has not reported yet.
- Non-Goal: judging required-check *results*. A required context that reported FAILURE is a
  genuine block and stays `blocked_product_decision` — a human owns it.
- Non-Goal: any change to `_watch_ci` or `_checks_registered` (owned by the archived
  `land-pr-wait-for-required-check-contexts`).
- Non-Goal: retrying or rerunning the outstanding check. The guard's existing rerun behaviour
  is unchanged; this change only decides how to classify the state it lands in.

## Decisions

- **Coverage here means "reported a terminal conclusion", not "present".** The watch's
  `_checks_registered` asks only for presence by name, which is the right question for "has CI
  started". At merge time the question is different: a context present-but-pending is exactly
  the state that makes `BLOCKED` mechanical rather than a decision.
- **Re-poll the merge-state guard rather than the rollup directly.** Re-entering
  `_merge_state_guard` keeps one source of truth for the merge state, keeps its CANCELLED-pair
  rerun behaviour available to a later poll, and lets a merge state that simply stops being
  BLOCKED fall through to the normal path with no special casing.
- **The budget is bounded and the exhausted outcome is `ceiling`/`failed_recoverable`.** This
  is the same posture the no-checks grace loop and #1306 already take: a reconcilable ceiling
  is safe, a guessed terminal classification is not. Completing the record as
  `blocked_product_decision` is the more destructive error precisely because it is terminal.
- **`None` and `[]` both mean "fall back to today's behaviour".** Identical to #1306's rule and
  for the same reason: blocking on a transient ruleset read failure would be the regression,
  and GitHub still enforces its own required checks at merge time.
- **Exact name matching against `name` or `context`.** The rollup mixes check runs (`name`) and
  commit statuses (`context`); `_merge_state_guard` already normalises both. A fuzzy match
  would let a nearly-named non-required check satisfy a required context, which is the bug.

## Risks / Trade-offs

- A genuinely, permanently blocked PR whose ruleset names a context that never runs will now
  spend the re-poll budget and report `ceiling` instead of `blocked_product_decision`. That is
  the intended direction of the trade: `ceiling` is reconcilable and loud;
  `blocked_product_decision` is terminal and silent.
- The re-poll adds up to a bounded number of extra `gh pr view` calls, and only on the BLOCKED
  path of a repo that has required contexts and incomplete coverage.
