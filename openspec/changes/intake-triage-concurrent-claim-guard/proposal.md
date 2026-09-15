## Why

Two concurrent triage applies for the same intake brief can each run the whole
`fold-into-change`/`propose-change` pipeline before either sees the other's work. In
`_worktree_pr_close()` (`src/worktrail/workqueue/queue_triage.py`), the brief is only
`claim()`ed at the very end, after a worktree has been created, a change authored, and a
pull request already opened via `router.land_pr`. Nothing before that point checks whether
the brief was already actioned, so two overlapping `apply --confirm` invocations for the
same brief id (a manual `/go <brief-id>` pickup racing the drain cron, or two drain
iterations overlapping) each independently fetch, branch, author a change, validate, and
push — and both succeed in opening a pull request before either process reaches its own
`claim()` call.

This happened live on 2026-09-10: intake brief `20260910-145452-make-ggb-bunny-storage-environment`
was evaluated and applied twice concurrently, producing two separate merged OpenSpec
proposal PRs for the same feature (`gracefully-giving-back#777` and `#778`) before either
invocation could observe the other's PR. The duplicate was cleaned up by hand
(`gracefully-giving-back#779` removed the newer change, keeping `#777`). `work_queue.py`'s
`claim()`/`release()` already provide an atomic `queue/` → `picked/` rename for exactly this
kind of race (used correctly by every other verdict path — `_apply_close()` claims first,
`_apply_work_directly()`'s frontmatter stamp, `_apply_needs_decision()`'s decision file); the
fold/propose apply path is the one path that defers the same primitive until after the
expensive, externally-visible work is already done.

## What Changes

- `_worktree_pr_close()` claims the brief (`claim(v.brief_id, by="queue-triage")`) as its
  first action, before any git fetch, worktree creation, change authoring, or PR work
  starts. A brief that is already claimed (by a concurrent apply, or anything else) returns
  an error result immediately — no worktree, no agent spawn, no pull request — instead of
  racing the other invocation through the pipeline.
- Any failure between the claim and a pull request actually existing (fetch, unpushed-base
  check, worktree creation, `prepare()`, `openspec validate`, `worktrail-compile`, or
  `land_pr` returning no PR URL) now releases the claimed brief back to `queue/`
  (`release()`), preserving the existing fail-closed contract: the brief remains in
  `queue/` with unchanged focus/evidence content and nothing is pushed.
- Once a PR URL exists, closing the brief is unchanged (`done(..., triaged_to=pr_url)`,
  with the existing `release()` rollback on `done()` failure) — the brief is already
  claimed by the earlier step, so the redundant `claim()` call that used to sit right
  before `done()` is removed.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `intake-triage`: the "Fold and propose are applied as a pull request, fail-closed"
  requirement gains a concurrency guarantee — the brief is claimed before any worktree/PR
  work begins, so a second concurrent apply for the same brief id is serialized out instead
  of racing to its own pull request.

## Impact

- `src/worktrail/workqueue/queue_triage.py`: `_worktree_pr_close()` claims first, releases
  on any pre-PR failure, and no longer re-claims immediately before `done()`.
- `tests/workqueue/test_queue_triage.py`: coverage for an already-claimed brief being
  skipped with no worktree/PR side effects, and for a pre-PR failure releasing the brief
  back to `queue/`.
- No change to `_apply_fold_into_change()`/`_apply_propose_change()`'s own signatures, to
  `apply_verdicts()`'s dispatch, or to any other verdict's apply path (`_apply_close()`,
  `_apply_work_directly()`, `_apply_needs_decision()`, `_apply_needs_update()`,
  `_apply_keep()`) — all already claim (or otherwise serialize) correctly before doing any
  visible work.
