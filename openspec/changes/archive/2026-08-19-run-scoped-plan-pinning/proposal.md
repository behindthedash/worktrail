## Why

A single `full-real` run can compile its `RunPlan` **more than once**, and each compile
can return a materially different plan. Because `coordinator.plan_groups()` derives group
membership from each task's `deps`/`files`, and policy derives agent/model routing from
each task's `purpose`/`complexity`, a second plan silently changes what the run is doing
mid-flight.

Root cause, verified against `main` @ `4096c9c`:

1. **The second compile is structural, not accidental.** `_pipeline_scheduler` →
   `apply_run_plan` (`live.py:3496`) → `compile_run_plan` is compile #1. After fan-out,
   when `integrate_complete`, `_dispatch_pending_tail` (`live.py:4307`) → `live_run_real`
   (`live.py:3398`) → `apply_run_plan` (`live.py:2689`) is compile #2. **Any run holding
   back a tail (`e2e`/`cleanup`/`docs`) task compiles twice by construction.** No caller
   anywhere in `src/`, `skills/`, `commands/`, `scripts/`, or `.github/workflows/` passes
   `--force`; both calls use `force=False`. Resume does not bypass compilation;
   `--from-verify` does not compile at all.
2. **The second compile can miss the cache.** `runplan.fingerprint()` hashes each task's
   `(id, title, kind, deps, files)` plus the content of every other file in the change
   dir. When anything in that input moves between the two calls, the tail-phase compile
   misses instead of hitting.
3. **A miss re-invokes an unseeded model.** `compile.py:453-455` → `_default_spawn`
   (`compile.py:342`) → `spawnlib.spawn_agent` (default `claude`/`sonnet`). `build_cmd`
   (`spawnlib.py:459-471`) passes **no `temperature`, `top_p`, or `seed`** — no such
   parameter exists anywhere in `src/`. `compile.py:376-378` already says so in-code.

Observed blast radius, from plans still on disk:

- `auto-dod-verification` (run `full-1786812908`): two plans 108s apart over the same 20
  task ids disagreeing on `deps`, `files`, `kind`, **`purpose`** (four tasks → empty
  string) and **`complexity`**. `purpose`/`complexity` feed `routing.purpose_tiers` /
  `routing.tiers`, so **agent and model routing were non-deterministic too**, not only
  grouping.
- `stacked-worktree-conflict-auto-resolve`: the second plan is a strict **subset** of the
  first — **19 → 15 tasks**, dropping `3.3`, `4.6`, `5.2`, `5.3`, with `5.1`'s dependency
  on the now-absent `4.6` rewired around. This is direct proof at the compile layer of the
  silent task-drop that brief `20260815-115257` could only infer.
- `model-tier-routing`: a third pair, ~12 minutes apart. The defect is **recurring**.

`_record_plan_fingerprint` (added by the preceding brief) already stamps
`plan_fingerprint` / `plan_fingerprints` into the run journal and warns on drift — but it
only **observes**. Nothing reads the stamp back, so nothing prevents the drift. Note also
that because it fires once per `apply_run_plan`, a drift warning is *expected* on ordinary
tail-bearing runs today, which is why "warn on drift" cannot be the whole answer.

## What Changes

Make a run's `RunPlan` **pinned for the life of that run** instead of recompiled per
phase. The storage already exists — the journal's `plan_fingerprint` — and the journal is
already the run's identity (`run_id` is stable across resumes and only resets when the
journal is absent), so the pin's lifetime is exactly the run's lifetime with no new
identifier and no signature change.

- `apply_run_plan()` SHALL consult the run journal's pinned `plan_fingerprint` **before**
  calling `compile_run_plan()`. On a pinned plan that loads from the plan cache, it uses
  that plan and performs no compile and no model call.
- With no pin recorded (the run's first `apply_run_plan`), behavior is unchanged: compile
  as today, and `_record_plan_fingerprint` stamps the resulting fingerprint, which becomes
  the pin for every later phase of the run.
- A pin that is recorded but whose plan cannot be loaded SHALL fail the run loudly rather
  than silently recompiling. Silently recompiling is precisely the defect: task worktrees
  have already been fanned out under the pinned plan, and replacing it underneath them is
  how a run ends up disagreeing with work already in progress — the same reasoning
  `compile_run_plan`'s existing `--force`-over-active-worktrees refusal already encodes.
  The error names the explicit re-plan escape hatch.
- `_record_plan_fingerprint`'s `PLAN DRIFT` warning stays as defense-in-depth and should
  simply stop firing in the normal path — the same posture the
  `runplan-collision-auto-repair` change took with `unordered_file_collisions()`.

This answers the originating brief's three questions: (1) the double compile is an
implicit tail-phase re-plan; (2) the inference pass is an unseeded model call and cannot
be made deterministic from our side, so the plan is pinned by content instead of pinned by
seed; (3) a run refuses to proceed only when a pin cannot be honored — under pinning the
ordinary drift case ceases to exist rather than becoming a hard failure on every
tail-bearing run.

## Capabilities

### New Capabilities
- `run-scoped-plan-pinning`: a run compiles its `RunPlan` at most once and reuses that
  exact plan for every later phase of the same run, including the tail-dispatch re-entry
  and resumes; an unresolvable pin fails the run instead of silently recompiling.

### Modified Capabilities
(none — this is new guard behavior around the existing `apply_run_plan` compile path; no
existing spec capability owns plan lifetime)

## Impact

- `src/worktrail/orchestrator/live.py` (`apply_run_plan`, and a small journal-pin reader
  beside `_record_plan_fingerprint`) — the pin read and the fail-closed path.
- `tests/orchestrator/test_apply_run_plan_autocompile.py`,
  `tests/orchestrator/test_plan_fingerprint_record.py` — new coverage for pin-hit,
  pin-absent, and pin-unresolvable; existing autocompile coverage stays valid as
  regression.

## Non-Goals

- Making the model call itself deterministic (no seed/temperature control exists in the
  provider path; pinning by content is the achievable equivalent).
- Changing `runplan.fingerprint()`'s inputs, the cache layout, or `plan_groups()`.
- Fixing the separate **cache-dir divergence** defect found while tracing this one:
  `compile.py:518` derives `repo` from `git rev-parse --show-toplevel` of the *spec dir*,
  so the skills' worktree-scoped `worktrail-compile` writes to
  `<worktree>-worktrees/runplans` while a later canonical `full-real` reads
  `<repo>-worktrees/runplans`, and the two caches never share entries. Real, but a
  different purpose — captured as its own handoff brief.
