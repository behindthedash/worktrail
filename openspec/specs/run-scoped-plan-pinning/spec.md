---
# run-scoped-plan-pinning Specification

## Purpose
TBD - created by archiving change run-scoped-plan-pinning. Update Purpose after archive.
## Requirements
### Requirement: A run reuses its pinned RunPlan instead of recompiling
When `apply_run_plan()` is called and the run journal for that `(repo, spec)` pair already
records a pinned `plan_fingerprint`, the system SHALL load the plan for that exact
fingerprint from the plan cache and merge it onto the freshly-loaded tasks, and SHALL NOT
call `compile_run_plan()` for that invocation. This applies to every later phase of the
same run, including the tail-dispatch re-entry (`_dispatch_pending_tail` →
`live_run_real` → `apply_run_plan`) and resumes of that run.

#### Scenario: Tail-dispatch re-entry reuses the run's first plan
- **WHEN** a run's first `apply_run_plan()` compiles a plan and records its fingerprint,
  and a later `apply_run_plan()` for the same `(repo, spec)` runs while that pin is present
  and its cached plan is loadable
- **THEN** the later call merges the pinned plan onto the tasks, performs no compile and no
  model call, and the tasks it returns carry the pinned plan's `deps`/`files` values

#### Scenario: Pinned reuse holds even when the change content has moved
- **WHEN** a pin is recorded, its cached plan is loadable, and the change directory's
  current content would now fingerprint differently from the pinned fingerprint
- **THEN** `apply_run_plan()` still merges the pinned plan and performs no compile, so the
  run's group membership and per-task routing inputs cannot change mid-run

#### Scenario: Resume of a pinned run does not recompile
- **WHEN** a run is resumed and its journal still carries the pin from the original run
- **THEN** the resumed `apply_run_plan()` merges the pinned plan and performs no compile

### Requirement: The first compile of a run establishes the pin
When `apply_run_plan()` is called and the run journal records no pinned
`plan_fingerprint`, the system SHALL compile (or take the existing cache/seed path)
exactly as it does today, and the resulting plan's fingerprint SHALL become the pin used
by every later `apply_run_plan()` call for that run.

#### Scenario: First call of a fresh run compiles and pins
- **WHEN** no journal pin exists for this `(repo, spec)` pair and `apply_run_plan()` is
  called
- **THEN** it compiles as before, and after the call the journal's `plan_fingerprint`
  equals the fingerprint of the plan that was applied

#### Scenario: A new run after the journal is gone compiles afresh
- **WHEN** the run journal for this `(repo, spec)` pair does not exist, so neither `run_id`
  nor a pin is present
- **THEN** `apply_run_plan()` compiles as before rather than reusing any previous run's
  plan, and pins the newly compiled fingerprint

### Requirement: An unresolvable or mismatched pin fails the run instead of recompiling
When the run journal records a pinned `plan_fingerprint` but no plan for that fingerprint
can be loaded from the plan cache, the system SHALL fail the call with an explicit error
naming the spec, the unresolvable fingerprint, and how to deliberately re-plan. The system
SHALL NOT silently compile a replacement plan in this case, because task worktrees may
already have been fanned out under the pinned plan. The same failure SHALL occur when the
pinned plan resolves but its task id set no longer matches the tasks just read from the
artifact: the system SHALL NOT fall back to `runplan.apply_to_tasks()`'s own drift-rejection
path (each task's own baseline deps/file-scope for the whole run), because that fallback
changes group membership and per-task routing inputs just as silently as a recompile would.

#### Scenario: Pinned plan file is missing from the cache
- **WHEN** the journal records a pin and `runplan.load_cached()` returns `None` for that
  fingerprint
- **THEN** `apply_run_plan()` raises an error whose message includes the spec id, the
  pinned fingerprint, and the documented way to clear the pin, and no compile is attempted

#### Scenario: Pinned plan resolves but the artifact's task set has drifted
- **WHEN** the journal records a pin, `runplan.load_cached()` returns a plan for that
  fingerprint, but the plan's task ids differ from the ids of the tasks just read from the
  artifact (a task was added, removed, or renamed since the plan was compiled)
- **THEN** `apply_run_plan()` raises an error whose message includes the spec id, the pinned
  fingerprint, the missing/unknown task ids, and the documented way to clear the pin, and
  `runplan.apply_to_tasks()` is never called for this invocation

#### Scenario: An unreadable journal does not block the run
- **WHEN** the run journal cannot be read or parsed at all
- **THEN** `apply_run_plan()` treats the run as having no pin and proceeds with its normal
  compile path, preserving today's behavior that journal I/O never takes a run down

### Requirement: Drift warning is retained as defense-in-depth
`_record_plan_fingerprint`'s existing `plan_fingerprints` accumulation and `PLAN DRIFT`
warning SHALL remain in place and SHALL continue to record every distinct fingerprint the
journal observes, so that a pin bypass or an unpinned code path is still visible.

#### Scenario: Normal pinned run records exactly one fingerprint
- **WHEN** a run with a tail phase completes under pinning
- **THEN** the journal's `plan_fingerprints` contains exactly one entry and no `PLAN DRIFT`
  warning is emitted
