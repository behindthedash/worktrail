## Why

`compile.py`'s model pass replaces an OpenSpec change's conservative authored `deps` with the plan's own edges, and the only ordering signal it is told to look for is shared file scope: the prompt's dependency re-check reads "for every file two or more tasks now declare in `files`, confirm those tasks are ordered". Nothing in `compile.py` mentions an explicit prose dependency sentence — `grep -rn "depends on" src/worktrail/conductor/compile.py` returns nothing.

Task authors do write those sentences. `openspec/changes/archive/2026-09-06-compile-failure-recovery-procedure/tasks.md:50` ends task 2.1 with "depends on 1.1, 1.2", and 2.1's file (`tests/test_plugin_surface.py`) is disjoint from 1.1's and 1.2's docs files. Because the file sets do not overlap, the shared-file heuristic sees nothing to order, and the compiled plan can drop the author's stated ordering entirely — dispatching 2.1 into the same frontier as the work it exists to depend on. The authored intent is present, in the artifact the compiler already reads, and is silently discarded.

## What Changes

- Extract explicit prose dependency references ("depends on 1.1, 1.2") from each task's authored text during compile, deterministically, without a model.
- Union those references into the compiled plan's `deps`, on both compile paths — the model pass and the no-model seeded path — so an authored ordering constraint can never be weaker in the compiled plan than in the artifact it was compiled from.
- Report a prose dependency reference that names no task in the change as a compile problem, so a typo'd reference is surfaced rather than silently dropped.
- Tell the model in `PROMPT` to honor an explicit prose dependency sentence as a real ordering constraint, so its own `deps` answer agrees with the deterministic union instead of contradicting it.

## Capabilities

### New Capabilities

- `compile-prose-dependency-references`: Defines how an explicitly authored prose dependency reference in a task's text becomes a compiled-plan dependency edge, on every compile path, and what happens when such a reference resolves to no task.

### Modified Capabilities

None.

## Impact

- Adds prose-reference extraction and a `deps` union to `src/worktrail/conductor/compile.py`, covering both `_plan_from_tasks()` (no-model path) and `_validate()` (model path), plus one new compile problem for an unresolvable reference.
- Extends the static `PROMPT` text in the same module.
- Adds extraction, union, unresolved-reference, and prompt-pinning coverage in `tests/conductor/test_compile.py`.
- Only ever adds edges; no authored or model-supplied `deps` entry is removed, so a compiled plan cannot become more parallel than it is today as a result of this change.
- Does not change `tasks.md` authoring rules, the `files` inference, or `runplan.py`'s edge-dropping safety rule.
