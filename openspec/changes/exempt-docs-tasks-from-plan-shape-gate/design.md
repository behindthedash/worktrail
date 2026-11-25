## Context

`shape_problems()` is the rejecting half of the plan-shape gate. It filters the merged task
list down to a fan-out, then computes a critical-path/width ratio, same-file dependent chains,
and missing test scope over that set. The filter is `kind not in TAIL_KINDS`, but `TAIL_KINDS`
is `{"e2e", "cleanup"}` -- `docs` was deliberately kept out of it because it is still
dispatched, not held out of the fan-out like a tail. The gate then judges docs tasks by rules
written for implementation work. Because a docs task almost always declares the change's own
`tasks.md` (where its checkbox lives) and OpenSpec's source derives within-group sequential
deps from file order, a routine docs chain looks exactly like a serial, same-file
implementation chain and is rejected.

## Goals / Non-Goals

- Goals: `docs`-kind tasks stop contributing to the serial and same-file rules; the chain
  ancestry used by those rules does not treat a docs task as a bridge; the no-fan-out rule and
  `profile()` keep their current behavior.
- Non-Goals: changing what `docs` means at scheduling time; adding `docs` to `TAIL_KINDS`;
  changing the advisory wall-clock estimate; adding a policy key or authoring guidance.

## Decisions

**D1. Exempt `docs` inside the gate, not by adding it to `TAIL_KINDS`.**
`TAIL_KINDS` is scheduling semantics read by `runnable_frontier`, `tail_held_out_task_ids`,
`plan_groups`, `runplan`'s file-scope handling, `needs_compile`, and the task-source adapters.
Adding `docs` there would hold docs out of the fan-out and move them into the serialized tail
phase after every implementation task, and would also change `plan_groups`, `runplan`'s
file-scope handling, and `needs_compile` -- a much wider behavior change than the defect, whose
only requirement is that the shape gate stop judging docs tasks. A module-level
`SHAPE_EXEMPT_KINDS = TAIL_KINDS | {"docs"}` local to `parallelism.py` expresses exactly the
needed statement: "not implementation work for the purpose of the fan-out shape gate".

**D2. Both rules share one gate fan-out.** The serial rule, the same-file writer map, and the
ancestor walk that chains them all read the same filtered set. Filtering `docs` out of that
set also drops dependency edges through it, so an implementation task depending on a docs task
that itself depends on another implementation task is not turned into a serial chain by
bookkeeping order. This matches how a tail-kind task is already treated.

**D3. No-fan-out behavior is unchanged.** The existing check already treats `docs` as
non-implementation (`any(kind not in TAIL_KINDS | {"docs"})`); spelling it through the new
constant is behavior-preserving. A docs-only plan is still rejected. A plan whose only pending
tasks are docs and whose implementation tasks are `completed` passes, which is the documented
re-compile case -- not a new acceptance.

**D4. `profile()` stays docs-inclusive.** `profile()` and `estimate_minutes()` are the advisory
wall-clock signal. Docs tasks are dispatched, so they consume real time; the summary should
keep counting them. Only the rejection gate ignores their bookkeeping shape.

## Risks / Trade-offs

- An implementation task mistagged `docs` now also escapes the serial/same-file rules. It
  already escaped review (`_review_exempt`), the missing-files check, and the
  missing-test-scope rule under that same tag, so the exemption follows the tag's existing
  meaning rather than creating a new blind spot. A plan made only of mistagged tasks still
  fails the no-fan-out rule.
- A genuinely serial docs chain is no longer flagged. Accepted: the gate protects
  implementation throughput, and docs work is short and review-exempt; its serialized cost is
  visible in the unaffected advisory estimate.
- No migration: the gate is computed at compile time from the merged task list, so the next
  compile of an affected change simply passes.

## Migration Plan

None. The change is a compile-time predicate over task metadata; no journal, config, or
artifact is rewritten.
