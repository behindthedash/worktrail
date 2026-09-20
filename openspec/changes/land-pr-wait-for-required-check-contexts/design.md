## Context

`_watch_ci` has two places where "there are checks" is decided: the `_NO_CHECKS_GRACE_ATTEMPTS`
poll loop at entry, and the `gh pr checks --watch --fail-fast` re-issue loop. Both treat the
presence of any check as sufficient. The required set lives in the repo ruleset, which
`router/automerge_preflight.required_status_check_contexts(owner_repo, branch, runner)` already
reads (`gh api repos/<slug>/rules/branches/<branch>`, `None` on a failed query, `[]` as the real
"nothing is required" answer).

## Goals / Non-Goals

- Goal: never report `settled` for a watch that only ever observed non-required checks.
- Non-Goal: enforce the required contexts' *results*. `--fail-fast` already classifies failures;
  this change is only about which checks must be present before the watch is trusted.
- Non-Goal: any change to head-SHA tracking (owned by
  `land-pr-watch-follow-head-sha-and-rebase-bot-commits`).

## Decisions

- **Resolve the contexts once, in `land_pr()`, not per poll.** The ruleset does not change
  mid-watch, and a per-poll `gh api` call would multiply the watch's API cost by the grace
  attempts. The resolved list is threaded into `_watch_ci` as a parameter, keeping `_watch_ci`
  a pure function of its inputs for the existing `FakeRun` tests.
- **`None` and `[]` both mean "fall back to today's behaviour".** `None` is a failed read;
  blocking a green PR on a transient ruleset read failure would be a regression, and
  `automerge_preflight.is_preflight_query_error` documents the same posture (GitHub still
  enforces its own required checks at merge time). `[]` is a branch with nothing required,
  where "any check" is the only available signal.
- **Missing coverage after the grace period is `budget_exhausted`, not `settled`.** This is the
  identical choice the no-checks grace loop already documents: a human or a retry (`ceiling`)
  is the safe default, a guessed pass is not.
- **A zero-exit watch with missing coverage re-enters the loop** rather than returning early,
  so a required context that registers late is still watched to its own result; the re-issue
  budget bounds it.
- **Name matching is exact on the check name.** `gh pr checks --json name` reports the check
  name, and ruleset contexts are the same strings for GitHub Actions job checks; a fuzzy match
  would let a nearly-named non-required check satisfy a required context, which is the bug.

## Risks / Trade-offs

- A ruleset whose context strings do not match the reported check names would never reach
  coverage and would report `ceiling` on every landing. Mitigated by the `None`/`[]` fallback
  only covering read failure, not mismatch -- so the failure mode is a loud, reconcilable
  `ceiling`, not a silent pass. This is the intended direction of the trade.
