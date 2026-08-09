## Why

`runnable_frontier()` (`orchestrator/coordinator.py`) treats a task's `deps` entry
as satisfied only once the referenced id appears in `done`. A `deps`/`depends_on`
value that references a task id which does not exist in the same spec (a typo, a
task that was renamed or deleted, or a same-spec id used where an
`external-dependencies:` cross-spec reference was intended) can never enter
`done`, so the task depending on it silently never becomes runnable — the
orchestrator run stalls with no diagnostic. Elsewhere in the same codebase the
identical unresolved reference is handled inconsistently: `compute_levels()`
and `plan_groups()` (`coordinator.py`) and `compile.py`'s `_validate()` all
silently filter (`if d in ids`) rather than reject, so an author gets no signal
at authoring time either. `taskformats/devkit/source.py`'s `load_spec()` and
`taskformats/openspec/source.py`'s `load()` both read `deps` straight from
frontmatter/checklist with no membership check against the loaded task set —
the same gap `validate_external_dependencies()` already closes for
`external-dependencies:`, just never extended to same-spec `deps`.

Devkit-format specs may also carry a free-form `decision-log.md` auxiliary file
(`check_spec_sync.py`'s `AUX_FILENAMES`) recording authoring decisions. Nothing
today cross-checks a task's dependency on a decision against that decision
actually existing and being resolved, so a task can be authored as contingent on
a decision that was never logged, or one still open, with no diagnostic before
the orchestrator dispatches it — the same "silently stall" failure mode, one
level up from the task graph.

## What Changes

- Add a `validate_dependencies(spec_id, tasks) -> List[str]` method to the
  `TaskSource` protocol (`taskformats/base.py`) returning human-readable
  diagnostic strings, empty when clean. Implement it for `devkit` and
  `openspec` sources:
  - **Unresolved same-spec dependency**: a task's `deps` entry that does not
    match any loaded task's `id` is reported by task id and the dangling
    reference, distinguishing it from a legitimate `external-dependencies:`
    cross-spec reference (which resolves through the existing
    `resolve_external_dependency()` path, untouched by this change).
  - **Decision-log dependency coverage** (devkit only — OpenSpec changes have
    no `decision-log.md` convention): a task may declare `decision-refs:` in
    frontmatter, naming one or more decision ids from the spec's
    `decision-log.md`. Validation reports a task whose `decision-refs:` names
    an id absent from `decision-log.md`, and separately reports one whose
    named decision is present but not in a resolved/decided state (the log's
    existing status convention, mirroring the `[NEEDS CLARIFICATION]` gate
    `dashboard.py` already enforces for spec-check). A `decision-refs:` value
    on an OpenSpec-format task is reported as unsupported, not silently
    ignored.
- Wire the new check into `worktrail-live precheck` (`orchestrator/live.py`),
  alongside the existing external-dependency WARN loop, so every diagnostic
  surfaces through the same pre-launch gate operators already read
  (`#precheck-gate`) before `full-real` dispatches any worker — not only after
  a run has already stalled.
- Add fixtures/tests covering: a valid same-spec dependency, an unresolved
  same-spec dependency, an externally-satisfied dependency (must NOT be
  flagged by the new same-spec check), a valid decision-ref, an unresolved
  decision-ref, and an open/undecided decision-ref.

## Capabilities

### New Capabilities
- `task-source-dependency-validation`: pre-orchestration validation of
  same-spec task dependencies and (devkit-only) decision-log dependency
  coverage, surfaced through `worktrail-live precheck` with actionable,
  per-task diagnostics instead of a silent orchestration stall.

### Modified Capabilities
(none — no existing `openspec/specs/` capability documents `TaskSource` loading
or the `precheck` gate today, so this lands as new rather than a delta)

## Impact

- `src/worktrail/taskformats/base.py` — new `TaskSource.validate_dependencies` protocol method.
- `src/worktrail/taskformats/devkit/source.py` — implementation, including `decision-refs:` parsing and `decision-log.md` cross-check.
- `src/worktrail/taskformats/openspec/source.py` — implementation (same-spec `deps` only; `decision-refs:` reported unsupported).
- `src/worktrail/orchestrator/live.py` — `precheck()` calls the new validator and WARNs per finding.
- `tests/taskformats/` and `tests/orchestrator/test_live.py` (or nearest equivalent) — new fixtures and coverage.
- No change to `runnable_frontier()`, `compute_levels()`, `plan_groups()`, or `compile.py`'s `_validate()` — this change adds an upfront diagnostic gate, it does not alter their existing (silent-filter) runtime behavior, which stays out of scope for this change.
