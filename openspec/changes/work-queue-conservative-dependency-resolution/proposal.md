## Why

Legacy and hand-authored briefs can contain malformed or ambiguous `blocked-by` values that the current resolver mistakes for satisfied stale references, allowing genuinely blocked work to become eligible for automatic pickup. Epic 002 Feature 2 adds the conservative runtime boundary needed to protect every queue consumer without rewriting existing briefs.

## What Changes

- Introduce an explicit dependency-resolution result that distinguishes done, active, stale, ambiguous, and malformed references.
- Treat only done and syntactically valid stale references as satisfied; fail closed for active, ambiguous, and malformed values.
- Preserve existing queue files unchanged while exposing resolver diagnostics to current warning consumers.
- Add focused regression coverage for queue, picked, done, stale, ambiguous, and comma-joined legacy reference states.

## Capabilities

### New Capabilities

- `work-queue-conservative-dependency-resolution`: Defines conservative runtime resolution and eligibility behavior for dependency references already stored in work-queue briefs.

### Modified Capabilities

None.

## Impact

- Affects dependency resolution and blocked-state checks in `src/worktrail/workqueue/work_queue.py`.
- Adds focused coverage in `tests/workqueue/test_work_queue.py`.
- Does not mutate queue briefs, change supported producer input, or expand diagnostics into new list/dashboard output surfaces reserved for Epic 002 Feature 3.
