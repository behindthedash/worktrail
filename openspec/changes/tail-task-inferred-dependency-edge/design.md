## Context

Both shipped non-devkit task sources build a tail-kind task's baseline `deps` inside one
forward pass over the artifact, so the set they union in is a *prefix* of the change's
non-tail tasks rather than the whole set:

- `OpenSpecTaskSource.load()` (`src/worktrail/taskformats/openspec/source.py:87-107`) appends
  to `non_tail_ids` as it walks `tasks.md` and reads it for a tail task at that moment.
- `SpecKitTaskSource.load()` (`src/worktrail/taskformats/speckit/source.py:30-43`) has the
  same loop with the same accumulator.

The docstring justifies the union as "depending on every preceding non-tail task is what
`coordinator.tail_held_out_task_ids()` needs to hold it back", but the holding is by `kind`
alone (`coordinator.py:83-84`) -- the inferred `deps` set is what the tail dispatch gate
(`tail-dispatch-require-merged-deps`, open) reads to decide whether the producer has landed.
The "preceding" qualifier is an artifact of the single pass, not a designed ordering rule, and
it is exactly the gap the 2026-10-03 incident fell through.

## Goals / Non-Goals

**Goals:**

- Make a tail-kind task's inferred `deps` cover every non-tail task in the change, in both
  task sources.
- Keep the change additive: no existing edge (within-group predecessor, authored `depends:`)
  is dropped, and non-tail inference is untouched.
- Pin the incident shape with a regression at the task-source boundary and at the compiled
  plan boundary.

**Non-Goals:**

- Changing the scheduler's two passes, the tail phase's ordering, or
  `tail_held_out_task_ids()`.
- Implementing the tail dispatch gate. That is `tail-dispatch-require-merged-deps`; this
  change only ensures the edge that gate reads actually exists.
- Inferring edges from a task's prose acceptance text, or from a path named in it (see
  Decisions).
- Rejecting or repairing the inverse shape (a non-tail task depending on a tail task). That
  is `tail-dependency-task-scheduling`.

## Decisions

### Fix the inferred edge, not the dispatch order

The brief offers three directions. Two of them act at the wrong layer:

- *Defer the tail task at dispatch when its acceptance names a path a pending non-tail task
  produces.* This is direction (c). It leaves the dependency graph wrong and compensates at
  runtime, so every other consumer of the graph -- the compile-time shape check, the plan's
  own reported edges, the dispatch-order listing an operator reads -- still sees a tail task
  with no relation to its producer. The sibling `tail-dispatch-require-merged-deps` change
  already establishes "the gate reads the edge"; a gate cannot do its job over an edge that
  was never created.
- *Scope the tail task's `files:` from its prose so the existing file-overlap pass sees it.*
  This is direction (b). `OpenSpecTaskSource` deliberately never invents scope -- its
  docstring is explicit that an invented scope is worse than none, because `runnable_frontier`
  reads an empty file set as "collides with nothing" and a guessed scope can make two tasks
  run concurrently over one file. It would also give an `e2e` task a *write* scope for a file
  it only reads, which the file-scope model has no way to express, and it only works when the
  producer happens to declare the exact path.

Direction (a) -- infer an edge from the prose acceptance -- is the right layer but the wrong
mechanism here: it needs path-to-task matching over free text, and the incident's acceptance
(`python3 scripts/check-claude-bypass-rules.py`) names no task id for the existing
`depends on <id>` prose scanner to find.

### The baseline is "all non-tail work", not "all earlier non-tail work"

A tail-kind task verifies or tidies up the change as a whole -- that is why it is held out of
the fan-out and run in the serialized tail phase after every implementation task. "Every
non-tail task in the change" is therefore the honest baseline, and the prefix form was an
under-approximation of it. Making the inferred set match the phase's own semantics removes the
gap without inventing a new rule: the same union, computed over the complete set rather than a
prefix. It is also the form the Spec Kit source was already trying to express.

Implementation is a two-phase pass in `load()`: collect the change's non-tail ids first, then
assign each tail task's `deps` as the sorted union of its within-group predecessor and that
whole set, with authored `depends:` still unioned on top. Non-tail tasks keep the existing
single-pass baseline, so the change is confined to the tail branch of each adapter.

*Alternative -- keep the prefix and add only the later-group ids that share a file with the
tail task:* rejected. It reintroduces the read/write confusion above, and it would make the
inferred set depend on the producer declaring scope it is not required to declare.

### The edge reaches the scheduler unchanged

`runplan.apply_to_tasks` only lets an inferred edge be dropped when *both* endpoints declare
file scope for the collision check to work with (`runplan.py:305-310`). A tail-kind task
declares no files, so the restored-edge branch keeps the new dependency in the merged plan. No
change to `runplan.py` is needed, and none is proposed.

### Apply it to both adapters

`SpecKitTaskSource` carries the identical accumulator and the identical consequence, and
`taskformats/` is the abstraction whose whole point is that a format plugs in without the
orchestrator knowing which one it is. Leaving one adapter with a known silent-miss bug while
fixing the other would make this change's own claim true for only half the surface. The
correction is the same four lines in each; both get their own focused test.

## Risks / Trade-offs

- **A tail task now waits for groups it previously ignored** → intended. The tail phase
  already runs after every implementation task, so the added edges constrain the tail
  *dispatch gate* rather than the fan-out, and they are what stop the incident's premature
  dispatch. A run that was previously letting a tail task verify a base without its producer
  was reporting a passing verification for work that had not landed.
- **A cached plan for an affected change becomes stale** → the plan fingerprint covers the
  change's content, so the next compile re-plans it. This is the normal cache-invalidation
  path, not a migration.
- **An authored `depends:` from a non-tail task onto a tail task now forms a cycle** → that
  graph is already stalled today (the non-tail task is held out of the fan-out and the tail
  task never dispatches during it), so no new failure is introduced; rejecting it outright is
  `tail-dependency-task-scheduling`'s job, and this change does not widen or narrow that.
- **A change that mixes tail and non-tail tasks inside one group** → the tail task takes both
  its within-group predecessor and the group's later non-tail tasks. The later tasks do not
  gain a back-edge, so the added edges are one-directional and cannot deadlock the group; a
  task-source scenario pins this.

## Migration Plan

None. No configuration, journal, or artifact-format change; the fix is confined to dependency
inference and is picked up on the next load or compile of a change. Rollback is a code revert.
