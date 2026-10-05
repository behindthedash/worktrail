## Why

A tail-kind (`e2e`/`cleanup`) task exists to verify or tidy up the change as a whole, so the
task sources give it an inferred `deps` set naming the implementation work it must follow.
The OpenSpec source collects that set in a single forward pass over `tasks.md`
(`src/worktrail/taskformats/openspec/source.py:87-107`): `non_tail_ids` is appended to as the
parser walks, so it only ever holds tasks that appear **earlier in file order**. A tail task
therefore never gets an edge to a producer that lives in a later group. `SpecKitTaskSource`
has the identical single-pass shape (`src/worktrail/taskformats/speckit/source.py:30-43`).

Verified live 2026-10-03, run `go-20261003-214938` (work-queue brief
`20261003-225749-tail-task-missing-dependency-edge`): in the devops change
`tighten-bypass-permissions-allow-rule`, `[e2e]` task 1.3's acceptance is
`python3 scripts/check-claude-bypass-rules.py` -- a script created by task 2.1, in a different
group. The compiled plan recorded `1.3 deps=1.1,1.2`; 2.1 was never an edge. The run
dispatched 1.3 into the tail phase against a base that did not contain 2.1's work, so the
reviewer and the fix worker both failed for missing context
(`missing context: 1.3 review -> [scripts/check-claude-bypass-rules.py]`, lines 154-155 of the
run log), the pipeline reported `LIVE RUN DONE: 9/10 tasks done` and exited 0, and 1.3 was
left unchecked -- a human ran the acceptance command by hand and flipped the checkbox.

The two open changes that touch tail ordering do not cover this, and must not be read as
fixing it: `tail-dispatch-require-merged-deps` *gates* tail dispatch on the groups its
declared `deps` name -- with no edge inferred the gate is vacuously satisfied and 1.3 still
dispatches; `tail-dependency-task-scheduling` covers the inverse shape (a non-tail task
depending on a tail task). Both assume the edge exists. This defect is that it was never
created.

## What Changes

- A tail-kind task's inferred baseline `deps` cover **every** non-tail task in the change,
  not only those earlier in file order. The within-group predecessor edge and any authored
  `depends:` entries are still unioned on top, so no edge the artifact already carried is
  lost.
- The same correction lands in the Spec Kit task source, which shares the loop shape and
  therefore the defect.
- Non-tail inference is unchanged: ordinary groups stay independent, and an implementation
  task's baseline stays its nearest preceding non-tail sibling in its own group.
- Regression coverage at both the task-source boundary and the compiled-plan boundary,
  shaped like the incident (a tail task whose producer lives in a later group).

## Capabilities

### New Capabilities

- `task-source-tail-dependency-inference`: a tail-kind task's inferred dependency set covers
  every non-tail task in the change, so a producer in a later group is never silently absent
  from the edge the tail-dispatch gate reads.

### Modified Capabilities

<!-- None. -->

## Impact

- `src/worktrail/taskformats/openspec/source.py`: `load()`'s dependency pass becomes
  two-phase (collect every non-tail id, then assign tail deps).
- `src/worktrail/taskformats/speckit/source.py`: the same correction to the shared loop.
- `tests/taskformats/openspec/`, `tests/taskformats/speckit/`, `tests/conductor/`: the
  incident shape, plus the unchanged non-tail behavior as a guard.
- No CLI, journal, or run-plan format change. A change whose tail tasks already carried the
  edge is unaffected; a change that was missing it now carries it, so its cached plan
  fingerprint changes and the next compile re-plans it. That is the point: the edge is what
  the existing tail-dispatch gate reads to hold the task until the producer's group has
  merged.
