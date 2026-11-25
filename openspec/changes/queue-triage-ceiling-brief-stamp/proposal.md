## Why

A `fold-into-change`/`propose-change` apply that reaches `router.land_pr` but gets back no pull
request URL loses two things at once: the reason, and the fact that anything was attempted.

- **The reason is dropped.** `_worktree_pr_close()`'s `refused` branch
  (`src/worktrail/workqueue/queue_triage.py:3290-3298`) reports
  `land_pr refused at <refused_step>: <detail>`, but the branch beside it
  (`:3300-3306`) returns only `land_pr <outcome> but no pr_url returned`, discarding
  `LandOutcome.detail`, `refused_step`, and `merge_result`. `LandOutcome`
  (`src/worktrail/router/land_pr.py:208-228`) carries all three precisely so a caller can report
  them.
- **The attempt is not recorded.** The outer `finally` (`queue_triage.py:3340-3345`) then
  `release()`s the claimed brief back to `queue/` because `pr_url` is empty, and `release()` sets
  only `status: queued` and `released-at` (`work_queue.py:1929-1936`). The brief is left
  byte-for-byte indistinguishable from one that was never applied.

The no-PR ceiling is never a pre-commit failure. Every `ceiling` return that carries no `pr_url`
is produced after `_commit_pending()` and a push attempt: `push_ambiguous`
(`land_pr.py:1596-1604`, "remote state unknown") or a refused PR create/update
(`land_pr.py:1638-1682`, with `refused_step` `pr_create`/`pr_update`/`risk_label_mismatch`). Every
other no-PR outcome is handled by the `refused` branch, and `landed`/`code_defect`/
`review_threads_blocking` always carry a URL. So in every case that reaches this branch a branch
has been committed and at least an attempt made to put it on the remote -- and the brief is
released carrying no trace of it.

That is a live incident, not a hypothetical. Work-queue brief
`20261122-210308-g6-route-check-false-positives` is back in `queue/` with `claimed-at`
`2026-11-22T21:14:41`, `released-at` `21:29:59`, and no `triaged-to`, yet its change landed as
behindthedash/millwright#70 (merged 2026-11-23; later deleted from `main` by #78). The opaque
error hid the real cause -- `gh pr create` refused to add the label `go:risk-low`, which did not
exist in that repo -- so the operator could not act on the failure; once the labels existed, the
landing succeeded out of band and the brief was left looking untouched.

Because the released brief carries no triage outcome, `is_recently_triaged()`
(`queue_triage.py:631-651`) sees nothing to dedup against, and the next `evaluate` run can
re-propose the same change. The deterministic branch name
(`_planned_fold_propose_branch()`, `:2902-2911`) means that re-proposal collides with the branch
the failed apply already pushed, after paying for a second authoring agent.

## What Changes

- **Report the landing ceiling's real cause.** The no-PR, non-`refused` branch of
  `_worktree_pr_close()` reports the outcome's `refused_step` and `detail` (falling back to
  `merge_result`, then to a plain no-URL message), and its verdict entry carries
  `rolled_back: true` and a `landing` sub-dict, so a caller sees that the brief was returned to
  the queue and why.
- **Stamp the brief before releasing it.** When that branch runs, `apply` records the failed
  landing on the claimed brief -- frontmatter `landing-failed-step`,
  `landing-failed-detail`, and `landing-branch`, plus a `## Triage <date>` note whose first line
  is `verdict: <the applied verdict type>` and which names the step, detail, and branch -- and
  only then does the existing `release()` return it to `queue/`.
- **The stamp is what prevents a blind re-propose.** The `## Triage` note is an ordinary triage
  outcome, so `is_recently_triaged()` now treats the released brief as recently triaged: a later
  `evaluate` within its dedup window excludes it from the evaluation groups and reports it as
  skipped instead of re-proposing the change. `apply` does not consult that window, so replaying
  the same verdict file after fixing the cause still works.
- **Document the operator recovery.** `skills/worktrail-go/SKILL.md`'s interactive triage step
  names the ceiling outcome and tells the operator to read the reported step/detail, fix the
  cause, and re-apply the same verdict file rather than re-evaluating the brief.
- **Non-goals:** obtaining a PR URL the landing did not produce; keeping the brief claimed in
  `picked/` (no PR exists, so the release is still right); changing the worktree/local-branch
  teardown; changing the `refused` branch, the pre-`land_pr` failures, the successful landing
  closure, or the rejected-closure stamps; changing `evaluate`'s dedup window or its escalation
  bypass; or synthesizing a `triaged-to` value from a branch name.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `queue-triage`: a `fold-into-change`/`propose-change` apply that reaches `land_pr` and gets no
  pull request URL reports the landing's refused step and detail, records the failed landing on
  the brief it releases, and thereby keeps a later `evaluate` from re-proposing the same change.

## Impact

- `src/worktrail/workqueue/queue_triage.py`: `_worktree_pr_close()`'s no-PR ceiling branch
  (message, entry shape, brief stamp/note before the existing release).
- `tests/workqueue/test_queue_triage.py`: `TestLandingTeardown` coverage for the ceiling report,
  the stamp and note, the replay path, the propose-change verdict line, a stamp failure, and the
  unchanged `refused` path.
- `skills/worktrail-go/SKILL.md`: the interactive triage apply step's failure guidance.
- No new console script, CLI flag, policy key, dependency, or journal schema. The three
  frontmatter fields are written through the existing `_set_fm_fields()`; `validate_brief` checks
  only a fixed required set and ignores unknown keys. The `## Triage <date>` note uses the
  existing note shape `triage_history()`/`is_recently_triaged()` already parse.
