## Context

See `proposal.md` — Why for the gap. Three existing pieces this design composes, all verified
against current code on this branch:

- `dashboard._pending_impl_stale` (`src/worktrail/router/dashboard.py:506`) — the devkit-format
  reference implementation: for each pending, non-tail task with `files:`, checks every declared
  file is git-tracked on the base branch and present on disk (`_task_files_are_shipped`,
  `dashboard.py:486`).
- `conductor.runplan` (`src/worktrail/conductor/runplan.py`) — `fingerprint(spec_dir, tasks)`
  content-hashes the task list plus every other file in the change directory; `load_cached`
  returns the `RunPlan` for that exact fingerprint or `None`; `apply_to_tasks(tasks, plan)`
  merges a plan's per-task `files`/`deps`/`kind` onto freshly-loaded tasks, rejecting the whole
  merge (not per-task) if the plan's task-id set has drifted from the artifact or if merging
  would create a dependency cycle.
- `orchestrator/live.py` — the orchestrator's own real pipeline: `compile_run_plan(...)` (may
  compile, i.e. call a model, on a cache miss) followed by `runplan.apply_to_tasks(tasks, plan)`.
  Dashboard's job is to reuse the second half of this pipeline (`apply_to_tasks`) while never
  reaching the first half's compile branch.

`OpenSpecTaskSource.load` (`taskformats/openspec/source.py`) returns task dicts with no `files`
key at all, by design (see `schema.py` module docstring) — file scope only ever exists in a
compiled `RunPlan`.

## Goals / Non-Goals

**Goals:**
- Give `_safe_detect_openspec` the same stale-bookkeeping protection `_pending_impl_stale`
  already gives the devkit path, for changes that have a matching cached `RunPlan`.
- Keep the dashboard scan's cost profile unchanged: local file reads and hashing only, no
  network, no model call, no write to the RunPlan cache.
- Reuse `runplan.apply_to_tasks`'s existing drift/cycle rejection rather than re-implementing a
  weaker cache-id-matching check.

**Non-Goals:**
- Triggering a compile from the dashboard scan path (`compile_run_plan` stays orchestrator-only).
- Changing `_pending_impl_stale`, `_pending_tail_stale`, or anything on the devkit path.
- Stale detection for OpenSpec tail-kind (`e2e`/`cleanup`) tasks. `_pending_tail_stale` covers
  this for devkit today via the same file-scope mechanism; extending it to OpenSpec is a natural
  follow-up but is out of scope here to keep this change to the `ready-to-implement` /
  `stale-bookkeeping` boundary the proposal actually targets. Left as an explicit gap, not
  silently dropped.
- Changing `RunPlan`, `TaskPlan`, `fingerprint`, `load_cached`, or `apply_to_tasks` themselves —
  all are consumed read-only.

## Decisions

**D1 — Reuse `apply_to_tasks` instead of reading `TaskPlan.files` directly.**
The proposal's own recommendation was to read `RunPlan.by_id()[task_id].files` straight off the
cached plan. Reusing `apply_to_tasks(tasks, plan)` instead is strictly better for the same
reason it exists in the orchestrator: it rejects the whole plan when the cached plan's task-id
set no longer matches the tasks freshly parsed from `tasks.md` (edited since the last compile),
or when merging the plan's edges would create a cycle. A raw `by_id()` lookup has neither check
and would happily apply a stale plan's file scope to a task list that has since changed shape.
This is the exact mechanism the real orchestrator relies on for the same cache before dispatching
a fan-out (`orchestrator/live.py`), so the dashboard's read-only check now trusts the cache under
the identical contract the orchestrator does.

**D2 — Locate the cache dir via `conductor.compile.default_cache_dir(repo)`.**
`compile.py`'s module-level imports are lightweight (stdlib + internal `worktrail` modules, no
model/SDK imports — `spawn` is injected as a callable, never imported), so importing
`default_cache_dir` from it costs nothing extra at dashboard-scan time and keeps the cache-path
convention (`<repo>-worktrees/runplans/`) in exactly one place. Rejected alternative: hand-roll
the path the way `router/quarantine_selfcheck.py`'s `_group_files` does
(`repo.parent / f"{repo.name}-worktrees" / "runplans"`, then glob newest-by-mtime). That helper
solves a different problem — recovering a *specific* group's file set for a *known* quarantined
spec_id, where "newest cache regardless of exact content match" is an acceptable, even desired,
loosening. Stale-bookkeeping detection is the opposite: trusting a cache whose fingerprint does
not match the change's *current* content is exactly the wrong direction to be wrong in (it could
mark a task stale using file scope computed against different tasks). `load_cached` on the exact
current fingerprint is the correct-by-construction choice here, matching `compile_run_plan`'s own
cache-hit path.

**D3 — Compute the fingerprint from the change directory and the freshly-loaded task dicts,
exactly as `compile_run_plan` does.** `fingerprint(spec_dir, tasks)` takes the same two inputs
`_safe_detect_openspec` already has in hand (`change_dir`, and `tasks` from
`_taskformats.load_spec`). No new data needs to be threaded in.

**D4 — Gate the whole check on `pending_impl > 0`, mirroring `probe_stale and pending_impl > 0`
in `detect_stage`.** The common case (a change fully implemented, or with no cached plan) should
do a fingerprint hash over the change directory's small file set — cheap, but still needless work
when there is nothing pending to reclassify. `_safe_detect_openspec` does not currently take a
`probe_stale` parameter and none is added: `detect_stage`'s own `probe_stale` defaults to `True`
and every caller in this codebase invokes it at that default, so gating purely on
`pending_impl > 0` (as `detect_stage` also does) keeps the new code path's behavior consistent
with the devkit path's actual current default, without adding a parameter nothing yet uses.

**D5 — Reject the two alternatives raised in the request, on the same grounds already verified:**
(a) grepping `git log` for the change slug in commit subjects — this repo's actual commit
history uses conventional-commit scopes, not change slugs, so the heuristic misses the exact
case it exists to catch, and unlike the RunPlan-cache approach it has no natural drift-rejection
story; (b) leaving this permanently out of scope — leaves `_pending_impl_stale`'s own documented
invariant ("These must NOT feed the orchestrator branch") unenforced for one of the two task
formats it should apply to uniformly.

## Risks / Trade-offs

- **[Risk]** A change that has never been compiled by the orchestrator gets no stale detection,
  even if its code did in fact ship out-of-band. → **Mitigation**: this is the documented
  cache-miss fallback, identical in spirit to `_pending_impl_stale`'s own "no task loader
  importable → []" conservative default. Never worse than today's unconditional
  `ready-to-implement`, and running the orchestrator once (which always compiles and caches)
  makes future scans benefit.
- **[Risk]** `apply_to_tasks`'s cycle/drift rejection is whole-plan: one drifted task blocks stale
  detection for every task in the change, not just the drifted one. → **Mitigation**: this
  matches the orchestrator's own risk tolerance for the identical cache (a drifted plan is
  distrusted wholesale there too), and drift only happens when `tasks.md`/`proposal.md`/
  `design.md`/`specs/**` changed since the last compile — exactly the case where trusting stale
  file scope would be most dangerous.
- **[Risk]** Importing `conductor.compile` and `conductor.runplan` into `dashboard.py` adds a
  new import-time dependency to a module used by the `/go` front door on every invocation. →
  **Mitigation**: both modules already have zero heavy/model-SDK imports (verified above); this
  mirrors `dashboard.py`'s existing `_HAVE_LOADER`-gated import pattern for the devkit task
  loader, so the same degrade-gracefully-if-unimportable posture applies here too.

## Migration Plan

Purely additive change to `_safe_detect_openspec`'s internals; no data migration, no schema
change, no config flag. Deploys as a normal merge to `main`. Rollback is a plain revert — the
cache files under `<repo>-worktrees/runplans/` are read-only inputs to this change and are
unaffected either way.
