## Why

`reconcile_unreconciled_tail_evidence` (`integrate.py`) opens or reuses a PR
for each unreconciled tail-kind (e2e/cleanup) task, but stops there: it never
constructs a `Verifier` for that PR. Every ordinary group PR built by the
pipeline scheduler (`live.py`'s `_pipeline_scheduler`, via
`_default_make_verifier`) goes through `Verifier.verify_one` — CI wait,
auto CI-fix, review-thread resolution, and merge. A tail reconciliation PR
gets none of that: it can sit red or with unresolved review threads
indefinitely, and nothing in the system will ever attempt to fix or merge it.
The `tail-task-auto-reconciliation` spec already commits to opening/reusing
the PR and recording the outcome, but is silent on CI verification — this is
a gap in that spec, not an implementation detail.

## What Changes

- Add a requirement to the `tail-task-auto-reconciliation` capability: a
  tail reconciliation PR that reaches `OPEN`/`already-open` state SHALL
  receive the same watch-until-green / resolve-review-threads / CI-fix /
  merge treatment (`Verifier.verify_one`) that an ordinary group PR receives.
- `reconcile_unreconciled_tail_evidence` gains an injectable verifier-factory
  seam (defaulting to a `_default_make_verifier`-equivalent construction:
  `resolve_spawn`/`ci_fix_spawn` via `_verifier_role_spawns`, sharing the
  pipeline scheduler's `git_lock`/`merge_lock`/`cumulative_regression` where
  the caller has them) and, after `integrate_one` yields an
  `OPEN`/`already-open` tail PR, runs `verify_one` against it using the same
  synthetic single-task group dict already built for `integrate_one`.
  Quarantined/merged/superseded findings are unaffected — there is no PR to
  verify in those cases.
- The `reconcile_state` a caller sees for a verified tail PR still reflects
  the post-integrate state (`opened`/`already-open`); verify's own outcome
  (merged, quarantined, or still open) is captured the same way group verify
  outcomes are today, via the group's journal record — no new
  `reconcile_state` value is introduced by this change.

## Capabilities

### Modified Capabilities
- `tail-task-auto-reconciliation`: add a requirement that an opened/reused
  tail reconciliation PR is watched, CI-fixed, and merged the same way an
  ordinary group PR is, instead of being left unverified after creation.

## Impact

- `src/worktrail/orchestrator/integrate.py`: `reconcile_unreconciled_tail_evidence`
  gains a verifier-factory parameter and a `verify_one` call per
  opened/reused tail PR.
- `src/worktrail/orchestrator/live.py`: the `_pipeline_scheduler` call site
  (`~line 4973`) passes its existing `make_verifier_fn` (or an equivalent
  factory reusing `iv_lock`/`pm_merge_lock`/`pm_cumulative_regression`) through
  to reconciliation instead of leaving the parameter at its default.
- New regression test asserting a tail-task PR eventually gets a VERIFY step
  (`tests/orchestrator/` — exact file follows existing
  `reconcile_unreconciled_tail_evidence` test layout).
