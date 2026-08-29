## Context

`reconcile_unreconciled_tail_evidence` (`integrate.py:1400`) already builds a
synthetic single-task group dict (`{"name": f"tail-{task_id}", "tasks": [task_id],
"depends_on": [], "reqs": ...}`) and feeds it to `integrate_one` — the same
seam every ordinary impl-group PR goes through. It stops there: it never
calls `Verifier.verify_one`. The only production construction site for
`Verifier` today is `_pipeline_scheduler`'s `_default_make_verifier`
(`live.py:4256`), invoked once per group inside that group's IV thread
(`live.py:4433`). The sole call site for `reconcile_unreconciled_tail_evidence`
is also inside `_pipeline_scheduler`, after the fan-out and tail-dispatch
phases complete (`live.py:4973`), where `make_verifier_fn`, `iv_lock`,
`pm_merge_lock`, and `pm_cumulative_regression` are already in scope. See
proposal.md - Why for why this gap matters.

## Goals / Non-Goals

**Goals:**
- A tail reconciliation PR that reaches `OPEN` gets watched, CI-fixed, and
  merged the same way a group PR does, sharing the run's existing
  cumulative post-merge gate and registry lock rather than building a second,
  disconnected verification path.
- No behavior change for findings that are `merged`, `quarantined`, or
  `superseded` before verification would apply — nothing to verify.

**Non-Goals:**
- No new merge-conflict-resolution, CI-fix, or quarantine logic — this reuses
  `Verifier.verify_one` unchanged.
- No change to `reconcile_state` vocabulary (`opened`/`already-open`/`merged`/
  `quarantined`/`superseded`) or to the `unreconciled_tail_evidence` journal
  schema beyond what verification's normal side effects (journal state
  transitioning to `MERGED`/`QUARANTINED`) already produce.
- Not addressing non-pipeline call paths: `reconcile_unreconciled_tail_evidence`
  has exactly one caller today (`_pipeline_scheduler`); no other scheduler
  path reconciles tail evidence, so none is touched.

## Decisions

**Injectable verifier-factory parameter, mirroring `_make_verifier`.**
`reconcile_unreconciled_tail_evidence` gains a `make_verifier: Optional[Callable[[], "Verifier"]] = None`
parameter. When the caller passes one (the pipeline scheduler will pass its
own `make_verifier_fn`), every tail PR is verified with a `Verifier` sharing
the run's `git_lock`/`merge_lock`/`cumulative_regression` — identical
serialization guarantees to ordinary groups verified in the same run. When
omitted, a local default builds a standalone `Verifier` via
`_verifier_role_spawns`-equivalent construction (imported lazily from
`live` inside the function, matching how `integrate.py` already imports
`live`/`verify` lazily elsewhere to avoid import cycles), with private
locks — correct for any future caller that reconciles tail evidence outside
the pipeline scheduler's context, and for tests that don't inject one.
Alternative considered: require every caller to always pass a factory. Rejected
— the existing `reconcile_unreconciled_tail_evidence` signature has no
required agent/model/timeout parameters, and every other optional
pipeline-only integration (e.g. `pr_labels`, `route`, `gates`) already
defaults to reasonable production behavior rather than making the caller
plumb it through by hand.

**Verify only PRs in `OPEN` state after `integrate_one`, using the existing
per-finding state read.** The function already reads `post_state` from the
group's journal record right after `integrate_one` to compute
`reconcile_state` (`opened`/`already-open`/`merged`/`quarantined`). Verify is
called exactly when `post_state == "OPEN"`, using the same `g` dict and the
`group_branch` value `integrate_one` established for that group's journal
record — no separate branch-resolution logic. This keeps the one place that
already classifies the four outcomes as the single decision point for
whether verification applies, instead of introducing a second classification.

**Verify runs synchronously per-finding, inside the existing per-finding loop.**
Tail reconciliation already loops findings serially (each `integrate_one` call
is sequential, not fanned out), and CI-fix/merge for one tail task has no
ordering dependency on another (`depends_on: []` on every synthetic tail
group). Running `verify_one` synchronously right after that finding's
`integrate_one` keeps the change localized to the existing loop body and
matches the loop's existing per-finding exception isolation (a `verify_one`
exception for one finding must not abort reconciliation of the rest).
Alternative considered: fan out tail verification across a thread pool like
the pipeline scheduler's per-group IV threads. Rejected as unnecessary scope
— tail evidence reconciliation is already a tail-of-run, low-volume path (one
entry per terminal tail task with stranded commits), and parallelizing it
would add pool-lifecycle complexity for no observed pipeline-scheduler-level
benefit in this path today.

**`verify_one` exceptions are caught the same way `integrate_one` exceptions
already are for this loop.** The existing per-finding `try/except` around
`integrate_one` is extended to also cover the new `verify_one` call, so a
verify-side exception for one finding is recorded as `quarantined` for that
finding alone (matching the docstring's existing "last line of defense"
per-finding isolation guarantee) rather than propagating and losing every
other finding in the batch.

## Risks / Trade-offs

[A tail PR's CI-fix worker competes with group CI-fix workers for the same
shared `git_lock`/worktree registry, since tail verification now runs on the
same `iv_lock` the pipeline scheduler's group IV threads share] → Already the
existing contract for every group `Verifier` in the run (`_git_lock` is
explicitly documented as serializing concurrent verification); tail
verification joining that contract is consistent with, not additional risk
beyond, the existing per-run serialization design.

[Verifying tail PRs serially (not fanned out) adds wall-clock time to the end
of a run proportional to the number of unreconciled tail findings] →
Bounded by design: tail findings are already deduplicated
(`_tail_superseded_by_map`) to only the DAG's leaf tasks, so the count is
small in practice; explicitly a Non-Goal to parallelize this path now.

## Open Questions

None — the single call site, the verifier construction pattern to match, and
the state at which verification applies are all confirmed from the current
code (see Context).
