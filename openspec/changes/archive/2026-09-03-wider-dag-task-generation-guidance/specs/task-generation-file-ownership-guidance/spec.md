## Purpose

Governs the guidance the `openspec-propose` skill's tasks-artifact authoring step applies when
deciding which task owns which file, so that a file recurring across an OpenSpec change's phases
is not independently rewritten by one task per phase, which otherwise collapses the task DAG's
parallel fan-out into a serial chain once grouping unions those tasks on the shared-file edge.

## ADDED Requirements

### Requirement: Per-Phase Hot-File Ownership Bias

When authoring `tasks.md`, the `openspec-propose` skill's tasks-artifact step SHALL bias task
decomposition so that a file expected to be written by tasks in more than one phase is owned by
at most one task within each phase, rather than left to accumulate one writer per phase by
default.

#### Scenario: A file is needed by tasks in three different phases

- **WHEN** the tasks-artifact step is decomposing work that would otherwise have a task in phase
  1, phase 2, and phase 3 each independently edit the same file (for example, a shared navigation
  registry)
- **THEN** the authored `tasks.md` SHALL assign at most one task per phase as the owner of that
  file, rather than assigning every phase's own task to edit it directly

#### Scenario: A file is only ever needed within a single phase

- **WHEN** a file is written by only one task in only one phase
- **THEN** the ownership-bias guidance SHALL NOT change how that task is decomposed — the bias
  only applies to files recurring across more than one phase

### Requirement: Per-Phase File Split For Additive Hot Files

When a hot file's per-phase writes are additive or composable (for example, appending entries to
a registry or data table rather than editing shared logic), the tasks-artifact step SHALL direct
decomposition into separate per-phase files, each owned by exactly one task, with a single
designated task later composing or merging them into the shared file.

#### Scenario: Registry entries added across phases

- **WHEN** each phase needs to add its own entries to a shared registry file, and the additions
  are independent of each other (no phase's entry depends on reading another phase's entry)
- **THEN** the authored `tasks.md` SHALL decompose the work into one per-phase file per
  contributing task, plus one task that composes those per-phase files into the shared registry
  file

#### Scenario: Per-phase writes are not additive

- **WHEN** a hot file's per-phase changes are not additive (for example, each phase must edit the
  same function body, not merely append independent entries)
- **THEN** the per-phase-file-split guidance SHALL NOT apply, and the Per-Phase Hot-File Ownership
  Bias requirement's single-owner-per-phase guidance governs instead

### Requirement: Collision-Serialization Preserved For Unavoidable Same-File Edits

This guidance SHALL NOT alter or weaken the existing grouping-time collision-serialization
behavior for any same-file collision the authoring-time bias does not avoid: tasks that still
share a file after decomposition continue to union into one lane at grouping time exactly as
before this guidance existed.

#### Scenario: A hot file collision remains after applying the guidance

- **WHEN** the ownership-bias and per-phase-file-split guidance still leave two or more tasks in
  the same phase needing to write the same file (for example, because the edits are neither
  independently ownable nor additive)
- **THEN** those tasks SHALL still be grouped into one lane by the orchestrator's existing
  shared-file union at grouping time, unchanged by this guidance
