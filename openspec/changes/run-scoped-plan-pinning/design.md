## Context

Evidence and root cause are in `proposal.md`. This document records the design decisions
and the alternatives that were rejected.

The controlling insight is that **the pin storage and the run identity already exist**:

- `journal_path_for(repo, spec_rel)` (`live.py:380`) gives one journal per `(repo, spec)`.
- `_journal_run_id` (`live.py:460-487`) returns the journal's existing `run_id` unchanged
  when the journal exists, and only mints a new one when the journal is absent or
  unreadable. **The journal therefore *is* the run's identity**, across resumes.
- `_record_plan_fingerprint` (`live.py:340`) already writes `journal["plan_fingerprint"]`
  on every `apply_run_plan`.

So a pin scoped to the journal is automatically scoped to the run, and requires no new
identifier, no new file, and no change to `apply_run_plan`'s signature. The only thing
missing is a **read** of a value that is already being written.

## Goals / Non-Goals

**Goals**
- One compile per run, reused by every later phase of that run.
- No silent plan substitution once work has been fanned out.
- Preserve today's behavior exactly on a run's first `apply_run_plan`.

**Non-Goals**
- Determinism of the model call itself (see DEC-002).
- Any change to `runplan.fingerprint()` inputs, cache layout, or `plan_groups()`.
- The cache-dir divergence defect (see "Adjacent defect", below).

## Decisions

### DEC-001 — Pin by content fingerprint in the journal, not by a new run-scoped file

The journal already carries `plan_fingerprint` and already has run lifetime. Reading it
back is a strictly smaller change than introducing a run-scoped plan copy, and it keeps a
single source of truth for "which plan is this run executing" — the same field the drift
warning already reports on.

*Alternative rejected:* copy the chosen plan into a run-scoped path at first compile. This
duplicates the cache, adds a second thing that can go stale, and gains nothing — the
fingerprint already addresses the plan uniquely and immutably.

### DEC-002 — Pin the plan by content rather than trying to make the model deterministic

`build_cmd` (`spawnlib.py:459-471`) exposes no `temperature`, `top_p`, or `seed`, and no
such parameter exists anywhere in `src/`. Seeded/temperature-0 compilation — one of the
options the originating brief asked us to weigh — is **not achievable from our side of the
provider boundary**, and would still not be guaranteed stable across model versions.
Pinning the compiled artifact gets the property we actually want (one run, one plan)
without depending on provider behavior we do not control.

### DEC-003 — An unresolvable pin fails closed

Once a pin exists, task worktrees may already have been fanned out under that plan.
Silently compiling a replacement is exactly the observed defect — it is how
`stacked-worktree-conflict-auto-resolve` lost tasks `3.3`, `4.6`, `5.2`, `5.3` between two
plans of the same change. `compile_run_plan` already encodes this reasoning for `--force`
("silently replacing that plan out from under it is how a resumed run ends up disagreeing
with the work already in progress"); this extends the same posture to the implicit re-plan
path, which is the one that actually fires in production.

A missing pinned plan is genuinely exceptional — it means the cache entry was deleted
mid-run — so failing loudly is recoverable, whereas a silent task drop is not.

*Alternative rejected:* fall back to the format's own `deps`/`files`. That changes group
membership just as much as a recompile does, so it fails to fix the defect while also
hiding it.

### DEC-004 — Journal I/O failure is not a pin

An unreadable or unparseable journal is treated as "no pin recorded", not as an error.
This preserves the existing invariant, stated in `_record_plan_fingerprint`'s own docstring
and in `apply_run_plan`'s `OSError` guard, that journal/cache I/O must never take a run
down. The fail-closed path in DEC-003 is reserved for the case where a pin was
successfully read and deliberately cannot be honored.

### DEC-005 — Re-planning is an explicit operator act

Because the pin is authoritative for the life of the journal, a deliberate re-plan means
clearing `plan_fingerprint` from the journal (or starting a fresh run, which means a fresh
journal). `worktrail-compile --force` alone is **not** sufficient: it writes a new cache
entry under a new fingerprint, but the pin still addresses the old one. The fail-closed
error message in DEC-003 names this explicitly so an operator hitting it is not left
guessing.

This is a deliberate trade: mid-run re-planning becomes harder, which is the point. The
previous behavior made it *implicit and invisible*.

### DEC-006 — Keep the drift warning

Per the precedent set by `runplan-collision-auto-repair`, the pre-existing detection stays
as defense-in-depth and simply stops firing on the normal path. It still catches a code
path that bypasses the pin. It is also the cheapest regression signal we have: a run whose
`plan_fingerprints` list grows past one entry means pinning was not honored somewhere.

Note this also resolves the pre-existing oddity that a `PLAN DRIFT` warning was *expected*
on every tail-bearing run — under pinning, one entry is the normal case, so the warning
becomes meaningful rather than routine.

### DEC-007 — Task-set drift on a resolved pin fails closed the same way

Task 1.1 implemented DEC-003 only for the *unresolvable* pin (plan cannot be loaded).
Observed live (brief 20260816-214009): a pin that resolves fine, but whose task ids no
longer match the tasks just read from the artifact, still fell through to
`runplan.apply_to_tasks()`'s own drift-rejection branch — precisely the "fall back to the
format's own deps/files" alternative DEC-003 already rejected, reached from a different
trigger. `apply_run_plan()` now compares the current tasks' ids against `plan.by_id()`
immediately after a pin resolves, and raises the same shape of error (spec id, pinned
fingerprint, missing/unknown ids, DEC-005 escape hatch) before ever calling
`apply_to_tasks()`. This closes the gap without touching `runplan.apply_to_tasks()` itself,
which stays a shared library function used by `compile.py`'s own diagnostic self-check and
`dashboard.py`'s read-only render — both of which compile from (and therefore never drift
against) the very tasks passed in, so the drift branch there remains correct for them.

## Risks / Trade-offs

- **A stale pin outlives a legitimate content edit.** If an operator edits `tasks.md`
  mid-run and resumes, the pinned plan is reused and the edit will not affect grouping.
  Mitigated by DEC-005's explicit escape hatch and the fail-closed message. Accepted:
  editing the artifact underneath fanned-out worktrees is already unsafe, and the pin makes
  the consequence visible instead of silently re-deriving the world.
- **Behavior change on the exceptional path.** `apply_run_plan` gains a way to raise. Its
  existing `OSError` guard and DEC-004 keep the ordinary I/O-failure paths non-fatal, so
  only the deliberate DEC-003 case is fatal.

## Adjacent defect (deliberately out of scope)

`compile.py:518` derives `repo` from `git rev-parse --show-toplevel` of the *spec dir*. The
skills run `worktrail-compile` against a change dir inside a task/spec **worktree**
(`pipeline-details.md:46`, `:197`; `subagent-prompts.md:578`), so the pre-compile writes to
`<worktree>-worktrees/runplans`, while a later canonical `full-real` reads
`<repo>-worktrees/runplans`. The two caches never share entries, so the pre-compile's work
is always thrown away and the run always pays for a fresh compile. This also explains why
the `auto-dod-verification` plan pair sits under a change-worktree `runplans/` directory
rather than the top-level one.

Real, verified, and worth fixing — but it is a cache-locality defect, not a plan-lifetime
defect, and fixing it here would widen this change's blast radius across the skills' compile
invocations. Captured as its own handoff brief. Pinning is correct and complete without it.

## Open Questions

- The precise mid-run mutation that made the tail-phase compile miss the cache in run
  `full-1786812908` was not recovered: the compiles ran against uncommitted worktree state,
  and the only in-window commit (`e1fa4f6`, 10:06:45) postdates both plans (09:51:10,
  09:52:58). Note `status` is deliberately excluded from the fingerprint
  (`runplan.py:171-175`), so ordinary checkbox ticking is *not* a sufficient explanation.
  This is **not a blocker**: pinning removes the run's dependence on the second compile
  agreeing with the first, whatever the specific mutation was. Left recorded so a future
  reproduction has a starting point.
