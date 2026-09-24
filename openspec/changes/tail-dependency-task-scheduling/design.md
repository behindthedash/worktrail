## Context

`parallelism.shape_problems()` is the settled-plan gate used by both
`worktrail-compile` and live precheck's no-LLM planning path. It already
returns human-readable diagnostics for task graphs that cannot be safely
scheduled, while precheck consistently formats those diagnostics as warnings.

## Goals / Non-Goals

**Goals:**

- Enforce the tail-dependency scheduling invariant at the shared settled-plan
  validation boundary.
- Preserve the current precheck diagnostic and exit-code path.

**Non-Goals:**

- Reorder the main and tail scheduler passes or run a second main pass after
  tail work completes.
- Change the separate tail dispatch merge gate or its journal state.
- Reject dependencies that involve only tail tasks, or historical completed work.

## Decisions

- **Add the rule to `shape_problems()`.** It sees the dependency graph that
  `full-real` will execute after the RunPlan has been applied, and its failures
  already reach both compile and precheck. A scheduler-only guard would leave
  `worktrail-compile` able to approve an unschedulable change; a parser-only
  rule would duplicate plan validation and miss inferred dependency edges.
- **Check direct dependencies of pending non-tail tasks.** The failure is the
  direct edge from work scheduled in the main pass to work intentionally held
  for the tail pass. Direct inspection names the actionable edge and leaves
  valid tail sequencing intact. Completed tasks are excluded because the
  scheduling risk no longer exists.
- **Return one deterministic diagnostic per invalid edge.** This permits an
  author to fix every reported task/dependency pair in one edit and matches
  the existing list-of-problems contract.

## Risks / Trade-offs

- [A previously accepted change becomes non-runnable] → The diagnostic names
  both endpoints and recommends removing the inversion or retagging work whose
  implementation was incorrectly marked as tail work.
- [A compiled plan changes dependency edges] → The check runs after
  `RunPlan.apply_to_tasks`, so it validates the exact graph that the scheduler
  receives rather than an earlier source representation.

## Migration Plan

No data migration is required. Authors correct an affected `tasks.md` graph
before launching or resuming a live run.
