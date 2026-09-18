## 1. Follow the PR head during the watch and rebase onto bot-only remote commits

- [ ] 1.1 In `src/worktrail/router/land_pr.py`: add `WATCH_HEAD_FOLLOW_MAX = 3` beside
      `WATCH_REISSUE_MAX` and a `_pr_head_sha(repo, pr_number, runner) -> str | None`
      helper wrapping `gh pr view <n> --json headRefOid` (None on non-zero exit, bad JSON,
      or empty SHA). In `_watch_ci`, read the head SHA before each blocking watch; after a
      non-zero exit re-read it, and when both reads succeeded and differ, increment a head
      change counter, reset the re-issue counter, skip classification, and re-enter the
      loop; when the head change counter exceeds `WATCH_HEAD_FOLLOW_MAX`, fall through to
      the existing `budget_exhausted` return. Add `_BOT_AUTHOR_RE` and
      `_rebase_onto_bot_commits(repo, branch, remote, runner) -> tuple[str | None, str]`:
      `git fetch <remote> <branch>` (a "couldn't find remote ref" failure returns
      `(None, "")`), `git rev-list --format=%ae HEAD..<remote>/<branch>` (empty or failed
      returns `(None, "")`), and when every author email matches `_BOT_AUTHOR_RE`,
      `git rebase <remote>/<branch>`; on non-zero rebase run `git rebase --abort` and return
      `("push", <rebase stderr or stdout>)`. Call it in `land_pr()` after `_push_target` and
      before `_push`, returning `LandOutcome(outcome="refused", refused_step="push",
      detail=...)` when it refuses.
      (Requirements: CI watch follows the PR head across bot pushes; Push rebases onto
      bot-only remote commits.)
      In a new `tests/router/test_land_pr_head_tracking.py` using `FakeRun` from
      `tests/router/test_land_pr.py`: cover each scenario of the first requirement against
      `_watch_ci` (bot push during watch does not consume a re-issue and settles on the new
      head; head moves past `WATCH_HEAD_FOLLOW_MAX` return budget exhausted; stable head
      still exhausts after `WATCH_REISSUE_MAX + 1` re-issues; failed head read counts the
      exit as before), and each scenario of the second against `_rebase_onto_bot_commits`
      and `land_pr()` (bot-only lead rebases then pushes; human commit skips the rebase and
      surfaces the non-fast-forward push detail; conflicting rebase aborts and refuses at
      `push` with the rebase output; missing remote ref and up-to-date remote both push
      without rebasing).
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr_head_tracking.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate land-pr-watch-follow-head-sha-and-rebase-bot-commits --strict` and
      `worktrail-compile openspec/changes/land-pr-watch-follow-head-sha-and-rebase-bot-commits`.
