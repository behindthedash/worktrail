## Context

`_watch_ci` treats every non-zero `gh pr checks --watch --fail-fast` exit as one re-issue
of a fixed budget (`WATCH_REISSUE_MAX = 3`), then classifies the `fail`-bucket rows. A bot
push to the PR branch cancels the in-flight run set for the previous head; `gh` reports the
cancelled checks in the `cancel` bucket, which the classifier ignores, so the loop `continue`s
and burns a re-issue. Three bot pushes (or one push while three separate runs are cancelled
one at a time) exhaust the budget within seconds and the pipeline reports `ceiling`. The
premise was confirmed in code but not reproduced against a live repo: reproduction needs a
repo whose workflow pushes to PR branches (continuum), which this checkout does not have.

`_push()` pushes `HEAD:<branch>` and reports any rejection as a `push` refusal. Once a bot
commit sits on the remote branch, every later invocation on the same local branch is
non-fast-forward until someone rebases by hand.

## Goals / Non-Goals

- Goals: a bot push during the watch never consumes watch budget or ends the watch early;
  a bot-only lead on the remote branch never turns a re-invocation into a `push` refusal;
  both stay bounded and fail closed.
- Non-Goals: changing what counts as transient vs code defect; auto-resolving rebase
  conflicts; rebasing over human commits on the remote branch; touching
  `orchestrator/verify.py` (already covered by `verify-ignore-cancelled-superseded-ci-runs`);
  changing the resume fast path's `head_sha` comparison (after a successful rebase, HEAD is
  the new SHA and the fast path simply does not apply, which is correct: the rebased head has
  not been pushed yet).

## Decisions

- **Follow the head, do not reclassify cancels.** Two options: (a) treat `cancel`-bucket rows
  as transient and rerun them; (b) read `headRefOid` before each watch and after each
  non-zero exit, and when it moved, restart the watch against the new head without spending a
  re-issue. (a) reruns work GitHub already superseded and still cannot distinguish a bot push
  from an operator cancel. (b) uses the one signal that explains the cancel. `_watch_ci` reads
  `gh pr view <n> --json headRefOid` (already the field `_merge_state_guard` queries) via a
  small `_pr_head_sha` helper; a read failure returns `None` and the watch behaves as today.
- **Separate bound for head moves.** `WATCH_HEAD_FOLLOW_MAX = 3` new heads per `_watch_ci`
  call. Each move resets the re-issue counter; a fourth move falls through to the existing
  `budget_exhausted` return. This keeps the ceiling outcome for a branch under continuous
  pushes and keeps total wall-clock bounded by `(WATCH_HEAD_FOLLOW_MAX + 1) *
  (WATCH_REISSUE_MAX + 1) * watch_timeout_s`.
- **Rebase only over bot-only commits.** New helper
  `_rebase_onto_bot_commits(repo, branch, remote, runner) -> tuple[str | None, str]` returning
  `(refused_step, detail)`. It runs `git fetch <remote> <branch>`; a fetch failure whose
  stderr names a missing remote ref (first push) returns `(None, "")`. Otherwise it computes
  `git rev-list --format=%ae <HEAD>..<remote>/<branch>`; an empty list returns `(None, "")`.
  If every author email matches `_BOT_AUTHOR_RE` (`\[bot\]@` or
  `@users.noreply.github.com` with a `[bot]` login, e.g. `github-actions[bot]`), it runs
  `git rebase <remote>/<branch>`. A non-zero rebase runs `git rebase --abort` and returns
  `("push", <rebase stderr>)`. Any non-bot author returns `(None, "")` and lets `_push` report
  the non-fast-forward itself, so a human's commits are never rewritten under them. The
  helper is called in `land_pr()` after `_push_target` and before `_push`; a returned
  `refused_step` short-circuits to the same `LandOutcome(outcome="refused",
  refused_step="push", detail=...)` shape `_push` produces.
- **Fail closed on everything else.** `rev-list` failure, unparsable output, or a fetch
  failure that is not "no such ref" all return `(None, "")` and let `_push` decide; the helper
  never makes a push succeed that would otherwise have been refused for a non-bot reason.

## Risks / Trade-offs

- A rebase moves local HEAD, so the run record's scope evidence (`<sha> on <branch>`) names
  the rebased SHA. That is the SHA actually pushed, which is the evidence the record wants.
- Bot detection is by author email pattern. A misidentified human commit would be rebased
  onto rather than lost (rebase preserves both sides); the risk is a conflict the operator
  must resolve, which is the status quo.
- The head-follow re-read costs one `gh pr view` per non-zero watch exit. Negligible next to
  a watch that runs up to `watch_timeout_s`.
