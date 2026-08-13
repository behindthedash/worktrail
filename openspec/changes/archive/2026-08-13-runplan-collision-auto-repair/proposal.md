## Why

`runplan.apply_to_tasks()` merges a compiled RunPlan onto a task list and enforces one
safety invariant (an edge may only be dropped when both endpoints declare file scope),
but it does not guarantee that two tasks sharing a file end up ordered. That gap is
caught by a separate assertion, `runplan.unordered_file_collisions()`, which both
`worktrail-compile` (standalone CLI) and `live.py`'s `validate_task_metadata()` (mid-run
fan-out gate) call and fail loud on. The only preventive measure today is a "Final pass"
instruction in `compile.py`'s `PROMPT` asking the model to re-check same-file tasks and
add a `deps` edge itself. When the model misses that instruction, compile or a live run
hard-fails and blocks until someone manually re-runs compile (`--force`) or hand-edits
task frontmatter. This has already happened in production (go-20260805-172326,
referenced directly in `runplan.py`'s and `live.py`'s docstrings) and is a recurring
failure mode, not a one-off.

## What Changes

- After `apply_to_tasks()` merges a plan onto tasks, deterministically close any
  remaining unordered same-file collision by inserting a `deps` edge between the two
  colliding tasks — the later task in authored order depends on the earlier one — instead
  of leaving the gap for a downstream caller to discover and fail loud on.
- The repair only fires on pairs `unordered_file_collisions()` already flags (same file,
  no ancestor relation in either direction); it never touches already-ordered pairs.
- If closing all such gaps would produce a cyclic dependency graph, treat it the same as
  today's other whole-plan rejection cases (task-set drift, plan/baseline cycle): reject
  the plan, fall back to the format's own deps and file scope, and record why in `notes`.
- `runplan.unordered_file_collisions()` and the existing fail-loud checks in
  `compile.py`'s `main()` and `live.py`'s `validate_task_metadata()` stay in place as a
  defense-in-depth assertion — they should simply stop firing in the normal path once
  `apply_to_tasks()` guarantees the invariant.

## Capabilities

### New Capabilities
- `runplan-collision-auto-repair`: `apply_to_tasks()` deterministically closes same-file
  ordering gaps left after plan merge, instead of relying solely on prompt-only
  prevention and failing the run when a model misses it.

### Modified Capabilities
(none — no existing spec capability owns this behavior yet)

## Impact

- `src/worktrail/conductor/runplan.py` (`apply_to_tasks`) — the merge/repair logic.
- `src/worktrail/conductor/compile.py` (`main`) — the standalone `worktrail-compile` CLI
  path; the ordering-gap error path becomes unreachable in the normal case but is kept.
- `src/worktrail/orchestrator/live.py` (`validate_task_metadata`) — the live fan-out
  gate; same effect.
- `tests/conductor/test_runplan.py`, `tests/conductor/test_compile.py` — new coverage
  for the repair path; existing collision-detection tests stay valid as regression
  coverage for the now-unreachable-in-practice fail-loud path.
