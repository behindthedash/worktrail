## Context

See `proposal.md` — Why. Design-relevant facts verified against the real
code and corpus:

- `compile_run_plan()` (`src/worktrail/conductor/compile.py`) is called from
  two sites: `main()` (the `worktrail-compile` CLI — the step-3 gate the
  brief and `pipeline-details.md#new-pipeline` name) and `live.py` (the
  orchestrator's internal fan-out path, run later, after the spec PR has
  already landed on base). Only `main()` currently turns a gap into a
  non-zero exit code (`needs_compile`/`unordered_file_collisions` via
  `_print_scope_gap_error`/`_print_ordering_gap_error`); `compile_run_plan()`
  itself never raises on a gap, it only degrades to a baseline plan.
- OpenSpec requirements are named (`### Requirement: <Name>`), not numbered
  like devkit's `REQ-001`. OpenSpec's `tasks.md` has no structured per-task
  field analogous to devkit's `reqs`/`ac-mapping`/`imp-requirements`
  frontmatter arrays (`OpenSpecTaskSource` emits `files: []` per task and
  nothing else task-identifying).
- The sibling `devkit-requirement-coverage-gate` capability's own committed
  spec (`openspec/specs/devkit-requirement-coverage-gate/spec.md`, merged
  today via PR #266/#270) asserts in its "Format Scoping" requirement that
  the OpenSpec path already has "equivalent coverage" from the existing
  scope-check. Confirmed false by reading `compile.py` in full: no
  requirement-name logic exists anywhere in it.
- `main()` already computes `merged`/`gaps`/`collisions` and combines them
  into one exit code before printing `--json` or human output; the natural
  integration point is a third check composed the same way, not a new CLI
  invocation.

## Goals / Non-Goals

**Goals:**
- Give OpenSpec-format changes a real requirement-coverage guarantee at the
  same step-3 gate the brief and `pipeline-details.md` already name, closing
  the gap the devkit sibling's spec incorrectly assumed was already closed.
- Reuse the devkit sibling's non-retroactive ratchet posture so the gate is
  adoptable without a migration step or baseline artifact.
- Correct the devkit sibling's now-false "Format Scoping" claim in the same
  change, so the two committed specs stay consistent with each other and
  with reality.

**Non-Goals:**
- Judging whether a task *adequately* implements a requirement — this checks
  that a name is referenced, nothing about implementation quality (same
  non-goal as the devkit sibling).
- Adding a structured per-task requirement-reference field to OpenSpec's
  `tasks.md` format. That would be a real format change with much broader
  blast radius (every OpenSpec-format repo, every `TaskSource` consumer) and
  is out of scope for closing this specific gate.
- Enforcing this inside `compile_run_plan()`'s internal `live.py` call path.
  The orchestrator's runtime fan-out is not the moment to fail a run over a
  documentation mismatch that should have been caught before the spec PR
  even merged; enforcement stays scoped to the `main()` CLI gate.
- Building a repo-wide audit mode for this gate. The devkit sibling's audit
  mode exists because devkit has a large pre-existing corpus with known gaps
  worth surfacing; this repo's own OpenSpec corpus is small and every change
  going forward is gated at creation, so there is no comparable backlog to
  audit into visibility.

## Decisions

### D1 — Name-presence text matching, not a structured field

A declared requirement is covered when its name string appears,
case-insensitively, anywhere in `tasks.md`'s raw text. No new `tasks.md`
field is introduced.

- *Alternative — add a structured `reqs:`-equivalent per task item (mirroring
  devkit's frontmatter arrays):* rejected. OpenSpec's `tasks.md` is a single
  checklist file, not one-file-per-task with frontmatter; inventing a new
  inline syntax for task-to-requirement linkage is a real format change that
  every `TaskSource` consumer and the `openspec` authoring CLI itself would
  need to understand, for a problem a text-presence check already solves for
  the demonstrated incident (a requirement with *zero* references anywhere).
- *Trade-off, accepted deliberately:* this is weaker than devkit's exact
  array lookup. A requirement named generically enough to appear by
  coincidence in unrelated task text is a possible false negative (reports
  covered when it is not); a requirement whose name is paraphrased rather
  than quoted verbatim in `tasks.md` is a possible false positive (reports
  uncovered when a human would consider it addressed). Both fail in the safe
  direction for the demonstrated incident class — a requirement mentioned
  *nowhere at all* is caught regardless of either failure mode — and both
  are explicitly named in Risks / Trade-offs below rather than engineered
  around, matching the devkit sibling's own "fails open, not closed" posture
  (D1's risk note in that change's `design.md`).

### D2 — New sibling module, composed into `compile.py`'s `main()`

The check ships as its own function/module (parsing `specs/**/spec.md` and
`tasks.md`), imported and composed inside `compile.py`'s `main()` alongside
the existing `gaps`/`collisions` checks — not inside `compile_run_plan()`
itself (see Non-Goals), and not as a new console script (unlike the devkit
sibling, this check has no reason to run standalone against a corpus; it is
inherently one-change-at-a-time, driven by the change directory `main()`
already receives as `a.spec`).

- *Alternative — extend `runplan.py`:* rejected. `runplan.py` owns file-scope
  application semantics (`apply_to_tasks`, `unordered_file_collisions`);
  requirement-name parsing is a distinct concern reading different files
  (`specs/**/spec.md`) that `runplan.py` never touches today.

### D3 — Non-retroactive ratchet via existing-spec diff, not git base-ref

The devkit sibling's ratchet (D2 in its own `design.md`) compares the current
working tree's declared-identifier set against the same spec at the base git
ref. This change's ratchet instead compares the change's declared
requirement names against the requirement names already present in
`openspec/specs/<capability-path>/spec.md` on disk (the already-merged main
spec for that capability, if one exists):

- A capability path with no existing `openspec/specs/<path>/spec.md` (a
  brand-new capability, `## ADDED Requirements` only) — every declared
  requirement is newly declared and enforced.
- A capability path with an existing main spec (`## MODIFIED Requirements`)
  — only requirement names absent from that existing main spec are newly
  declared and enforced; a modified requirement that already existed by that
  exact name is never newly declared, even if its text changed.

- *Alternative — git base-ref comparison, matching the devkit sibling
  exactly:* rejected for this check specifically. An OpenSpec change
  directory is itself new/uncommitted at compile time (it lives on
  `spec/$SPEC_ID`, not yet merged), so there is no meaningful prior version
  of `specs/**/spec.md` *within the change* to diff against — the
  change-directory content did not exist at the base ref at all. Comparing
  against the already-merged `openspec/specs/` tree is the equivalent
  "what already existed" baseline for this format, and is exactly what
  `openspec archive` itself later reconciles against.
- *Trade-off:* a change that edits `## MODIFIED Requirements` text
  substantially, without renaming the requirement, is never re-checked for
  coverage even if the edit invalidates prior task references. Accepted: the
  devkit sibling accepts the equivalent trade-off (its D2 Risks note "Renaming
  an identifier reads as one removal plus one addition" as intended
  behavior, not a gap) and re-deriving coverage on every edit would make the
  gate retroactive in practice, defeating D3's own purpose.

### D4 — Gate applies only when the compiled spec directory is OpenSpec-format

The check runs only when `a.spec` resolves to an `openspec/changes/<id>/`
directory (has `specs/` and/or `tasks.md` in that layout); a devkit-format
`docs/specs/<id>/` directory is a no-op here, symmetric with the devkit
sibling's own "Format Scoping" requirement (now corrected by this change) —
each format's coverage gate owns exactly one path and defers to the other
capability for the format it does not own.

## Risks / Trade-offs

- **Name-presence matching produces a false negative (coincidental
  substring match) or false positive (paraphrased reference)** → Accepted,
  documented in D1. Fails open on the false-negative direction (same
  posture as the devkit sibling), and a false positive is a compile failure
  an author can resolve by referencing the requirement's exact name once in
  `tasks.md` — a low-cost false-positive fix compared to a silent gap.
- **A capability rename (proposal renames `<capability-path>` while keeping
  requirement names) reads as a brand-new capability with no existing main
  spec** → Every requirement is treated as newly declared and enforced,
  which is the safe direction (over-enforcement, not under-enforcement).
- **A change spans multiple capability paths, one new and one modified** →
  Each capability path's delta is evaluated against its own
  `openspec/specs/<path>/spec.md` independently; there is no cross-
  capability aggregation to get wrong.

## Migration Plan

No migration artifact. The gate activates on this repo's next
`worktrail-compile` invocation against an OpenSpec change; by D3's ratchet it
enforces only this change's own newly-declared requirements going forward,
never retroactively. Rollback is reverting `compile.py`'s `main()`
composition of the new check; the parsing function can remain in the module
unused.
