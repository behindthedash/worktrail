## Why

`_worktree_pr_close()` (`src/worktrail/workqueue/queue_triage.py:3023`) lands a
`fold-into-change`/`propose-change` change via `router.land_pr`, then closes the brief with
`done(v.brief_id, note=v.evidence, triaged_to=pr_url)`. When that `done()` call returns
anything other than `status == "done"`, the code releases the brief back to `queue/`
unconditionally (`queue_triage.py:3323-3326`) — past the `if not pr_url:` guard at line 3304
that already scopes release to the no-PR case. A brief is therefore rolled back **after its
pull request exists** and, for a `landed` + `completed_and_merged` landing, already on the
base branch. The merged change's brief is then re-triaged as fresh work: the same fold gets
applied a second time, opening a duplicate PR for work already on base.

Reproduced 2026-10-03 against HEAD `7f81911c`: driving `apply_verdicts([verdict], confirm=True)`
with a `LandOutcome(outcome="landed", final_status="completed_and_merged", pr_url=...)` and
`queue_triage.done` stubbed to return `{"status": "unverified_reverification_claim", ...}`
leaves the brief back in `queue/` with its PR merged. This is the fail-mode PR #1400 left open:
#1400 removed one negation-blind *trigger* of the closure-evidence gate, not the rollback
itself.

## What Changes

- Once a PR URL exists, a rejected `done()` SHALL NOT release the brief back to `queue/`. The
  brief stays claimed in `picked/` with `status: picked`, carrying the rejected closure, so the
  stalled-in-flight resume path (`router/dashboard.py`'s `inflight_briefs`) can close it
  against the real pull request instead of a fresh triage re-landing the same work.
- The rejection's action-log entry keeps `pr_url`, `branch` and `landing`, names the rejection,
  and reports `rolled_back: false` — never a release.
- Unchanged: a failure with no PR URL (refused landing, bootstrap failure, unresolvable base
  branch) still releases the brief back to `queue/` exactly as today, via the single
  `if not pr_url:` release in the cleanup `finally`. The release is not called twice.
- Do NOT satisfy the closure-evidence gate by synthesizing a fenced transcript or similar
  structured note. The gate requires real evidence; this triage path has no re-run transcript
  of its own, and a fabricated one would defeat the gate rather than fix the defect.
- Deliberately out of scope: `queue_triage.py:2285` (the `stale-close`/`duplicate-of`
  claim+done path). It has a superficially identical `done()`-reject-then-`release()` shape,
  but it opens no PR at all and its docstring documents the release as deliberate. The design
  records the distinction so a later reader does not "fix" it too.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `queue-triage`: the "Merged fold/propose landing tears down its local branch" requirement
  gains the explicit contract that a rejected `done()` after a PR exists leaves the brief
  claimed in `picked/` (never released), with the no-PR release behavior stated as unchanged.

## Impact

- `src/worktrail/workqueue/queue_triage.py`: `_worktree_pr_close()` — the `done()`-rejection
  branch no longer calls `release()`; its docstring's "with rollback (`release()`) on `done()`
  failure" line is corrected.
- `tests/workqueue/test_queue_triage.py`: `TestLandingTeardown` gains a regression case for the
  merged-landing rejection (brief stays in `picked/`, `rolled_back: false`) and a companion
  case pinning the no-PR `refused` path's release.
- No CLI, frontmatter, or task-format change; no change to the `land_pr` call or to the
  cleanup `finally`.
