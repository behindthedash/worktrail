## Context

`runplan.apply_to_tasks(tasks, plan)` (src/worktrail/conductor/runplan.py:232) merges a
compiled `RunPlan` onto freshly-loaded tasks and enforces one invariant: a dependency
edge may only be dropped when both endpoints declare file scope. It builds `merged` in
the caller's original (authored) order, then calls `compute_levels(merged)` to reject the
whole plan on a cycle.

`runplan.unordered_file_collisions(tasks)` (runplan.py:327) is a separate read-only
assertion: it walks the merged task list's `deps` ancestry and flags any pair of tasks
that declare the same file with no ancestor relation between them. It is currently called
in exactly two places, both *after* `apply_to_tasks()` has already returned, and both
treat a non-empty result as fatal:

- `compile.py main()` (line 419): prints `_print_ordering_gap_error` and returns exit 1
  from the standalone `worktrail-compile` CLI — this blocks the orchestrator invocation
  step in `subagent-prompts.md#orchestrator`, which runs `worktrail-compile ... || exit 1`
  before every OpenSpec `full-real` launch.
- `live.py validate_task_metadata()` (line 466): raises `RuntimeError` mid-run, refusing
  to fan out pending tasks with an unresolved collision (go-20260805-172326).

The only preventive measure today is the "Final pass" paragraph in `compile.py`'s
`PROMPT` (line 149) asking the model compiling the plan to re-check same-file tasks and
add a `deps` edge itself. It is a prompt instruction, not a guarantee — a missed pass
reaches one of the two fail-loud sites above and blocks the run until a human re-runs
`worktrail-compile --force` or hand-edits task frontmatter.

## Goals / Non-Goals

**Goals:**
- Guarantee, deterministically and without an extra model call, that `apply_to_tasks()`
  never returns a merged task list containing an unordered same-file collision.
- Preserve the existing whole-plan-rejection philosophy: if closing collision gaps would
  itself require a cyclic graph, reject the plan the same way task-set drift and
  plan/baseline cycles are already rejected today (fall back to the format's own deps and
  file scope; record why in `notes`).
- Keep `unordered_file_collisions()` and both existing fail-loud call sites in place as
  defense-in-depth — they should simply stop firing in the normal path. **Correction from
  initial drafting:** "stop firing" means `compile.py main()`'s own collision branch
  becomes unreachable through its normal `apply_to_tasks()` → `unordered_file_collisions()`
  call sequence, which makes the two existing CLI-level tests that construct exactly that
  scenario and assert `rc == 1` (`test_the_cli_fails_loudly_on_an_unordered_file_collision`,
  `test_the_cli_json_mode_fails_loudly_on_an_unordered_file_collision`) test dead behavior
  once this change lands — they must be rewritten to assert the new auto-repaired outcome,
  not left as passive regression coverage. `live.py`'s `validate_task_metadata()` is
  different: it can observe a merged task list assembled without going through
  `apply_to_tasks()` at all, so its own existing test coverage stays valid unchanged.

**Non-Goals:**
- Changing the "Final pass" prompt instruction in `compile.py` — it stays as a
  cost-saving measure (a model that gets it right needs no repair) but is no longer the
  only thing standing between a missed instruction and a hard failure.
- Changing how `unordered_file_collisions()` itself computes ancestry, tail-kind
  exclusion, or empty-scope exclusion — the repair reuses it unchanged as the detector.
- Touching `needs_compile()`/the scope-gap error path (`_print_scope_gap_error`) — that is
  a distinct invariant (missing file scope entirely) already enforced elsewhere and out
  of scope for this change.

## Decisions

**Where the repair runs: inside `apply_to_tasks()`, after `merged` is built, before the
cycle check.** `merged` is already produced in the caller's original (authored) order —
the `for t in out: ... merged.append(m)` loop preserves it — so "later task in authored
order" can be read directly off `merged`'s index, with no extra state to carry in from a
caller. Running the repair inside `apply_to_tasks()` (rather than as a separate function
each of the two call sites would have to remember to invoke) makes the guarantee apply to
every current and future caller of `apply_to_tasks()` automatically — the exact "prompt
prevention becomes guaranteed" the proposal asks for. Alternative considered: a standalone
`close_unordered_collisions(tasks)` function each call site invokes explicitly. Rejected —
it re-introduces the same "callers must remember" gap this change exists to close, just
one level down.

**Repair happens before the cycle check, not after.** `compute_levels(merged)` already
runs once, after merge, to catch a cycle between the plan's edges and the baseline's. The
repair edges are computed from a fixed, non-adversarial total order (authored position in
`merged`), always pointing later→earlier, so they cannot form a cycle *among themselves*.
But whether they interact safely with an already-cyclic merge is exactly what the existing
`compute_levels(merged)` call is for — so the repair augments `merged`'s deps first, then
the existing cycle check runs once, unchanged, over the augmented graph. No new cycle
-detection code is needed.

**Edge direction: later authored-order task depends on the earlier one.** This matches the
proposal's stated behavior and the existing "Final pass" prompt's own instruction ("the
later task in authored order depending on the earlier one"), so a model-authored edge and
an auto-repaired edge for the same gap are indistinguishable in the merged output.

**Only mutate the two colliding tasks' `deps`; never touch `files`.** The collision
detector's file-scope requirement is unchanged — a repair only adds an ordering edge, it
never invents or removes file scope. This keeps the repair's blast radius limited to
exactly the invariant it exists to close.

**Idempotent by construction.** If the repair adds `a` to `b`'s `deps`, `b` is now `a`'s
descendant; `unordered_file_collisions()` reading the augmented graph on a hypothetical
second pass would find them ordered and not re-flag the pair. No dedup bookkeeping is
needed beyond the existing `deps = sorted(keep | restored | ...)` set-union pattern
`apply_to_tasks()` already uses.

**Rejection path reuses the existing whole-plan-rejection shape.** If augmenting `merged`
with repair edges makes `compute_levels(merged)` raise, take the exact same branch the
plan/baseline-cycle case already takes: append a `notes` entry explaining why, and return
`([dict(t) for t in tasks], notes)` — the original, unmodified tasks. This is deliberately
conservative: a plan that requires a cyclic repair to close its own collision gaps has
already demonstrated it cannot be fully trusted, matching the module's stated philosophy
("trusting half of that is worse than trusting none of it").

**Record what was repaired in `notes`.** `apply_to_tasks()` already appends a summary note
("run plan applied ...: N/M tasks scoped, K loosened"). Add a second note when repairs
occurred (count and the file/tasks involved) so the run journal — the only durable record
of a plan being distrusted or, now, repaired — reflects the repair the same way it already
reflects loosening.

**`unordered_file_collisions()` and its two call sites are unchanged.** They remain a
correctness backstop: if a future bug in the repair, or a caller that constructs a merged
task list without going through `apply_to_tasks()`, leaves a collision, the existing
fail-loud behavior still catches it. Removing them would trade a guaranteed invariant for
an unverified one.

## Risks / Trade-offs

- [Risk] A repaired edge changes scheduling — a task that previously could start
  immediately (if a model missed its file overlap) now waits behind another task's
  completion. → Mitigation: this is the intended, safe behavior — it is exactly what the
  "Final pass" prompt asks the model to do when it does catch the gap; the repair only
  makes it happen every time instead of only when the model succeeds.
- [Risk] Silent behavior change: a run that previously hard-failed (surfacing a compile
  gap to a human) now proceeds automatically. → Mitigation: the `notes` entry makes the
  repair visible in the run journal, and `worktrail-compile`'s non-`--json` text output
  already prints every `notes` entry — the signal moves from "run blocked" to "run
  proceeded, and here is what changed," matching the proposal's explicit intent.
- [Risk] A pathological plan needing many repair edges to close many collisions could
  significantly serialize what would otherwise be a highly parallel run. → Mitigation:
  out of scope for this change — the existing "run plan applied ...: K loosened" note
  already surfaces serialization from the *existing* file-scope invariant, and repair
  edges are additive to that same signal, not a new failure mode to design around.

## Migration Plan

No data migration. Purely a behavior change inside `runplan.apply_to_tasks()`, gated by
normal PR review and the repo's `pre_pr_cmd` (pytest + orchestrator record/replay check).
Rollback is a plain revert — the change adds no new persisted format or cache-key bump
(`PLAN_VERSION` is unchanged; the repair operates on already-loaded tasks, not on the
cached `RunPlan` itself).

## Open Questions

None outstanding — the "how" (function boundary, edge direction, rejection shape) is
established by this design; implementation detail (exact loop, note wording) is left to
`tasks.md`.
