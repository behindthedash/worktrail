## Why

An OpenSpec change can declare a requirement in its `specs/**/spec.md` delta
(an `## ADDED Requirements` or `## MODIFIED Requirements` section with a
`### Requirement: <Name>` header) that no task in `tasks.md` ever addresses,
and nothing in the toolchain notices. `worktrail-compile`'s step-3 scope-check
— the gate `pipeline-details.md#new-pipeline` step 3 runs against every
OpenSpec change before its docs-only spec PR is pushed — only infers per-task
`files`/`deps` (file scope and execution ordering); reading its full source
(`src/worktrail/conductor/compile.py`) confirms it contains no logic that
reads a requirement name, or cross-checks it against `tasks.md`. `openspec
validate` likewise only checks structural formatting (scenario-heading count,
requirement-without-scenario) — never requirement-to-task coverage.

This is the same incident class `docs/specs/001-task-ac-verification-gate`'s
DEC-009 documents in datalena (`REQ-023..028` declared with zero task
coverage, discovered only by a manual grep months later). The devkit-format
side of this exact gap was closed today by the
`devkit-requirement-coverage-gate` capability (`check_req_coverage.py`, wired
into `pre_pr_gate.py`) — but that capability's own committed spec asserts, in
its "Format Scoping" requirement, that "equivalent coverage is already
enforced by the existing \[OpenSpec] scope-check". That assertion is false: it
was written on the belief that `worktrail-compile`'s file/dependency inference
already covers requirement coverage, not on a direct reading of what that
inference actually checks. The OpenSpec path currently has no requirement
coverage guarantee at all.

## What Changes

- Add a requirement-coverage check for OpenSpec-format changes: parse every
  requirement name declared under `## ADDED Requirements` / `## MODIFIED
  Requirements` in a change's `specs/**/spec.md` delta files, and check
  whether each name is referenced anywhere in that change's `tasks.md`.
- Unlike devkit specs (which declare numeric `REQ-`/`AC-` identifiers cross-
  referenced through structured `reqs`/`ac-mapping`/`imp-requirements` task
  frontmatter), OpenSpec requirements are identified by a free-text `###
  Requirement: <Name>` header and OpenSpec's `tasks.md` carries no such
  structured per-task reference field (`OpenSpecTaskSource` always emits
  `files: []` and there is no `reqs:`-equivalent). Coverage is therefore
  **name-presence matching** against `tasks.md`'s full text, not structured
  array lookup — a deliberately weaker, fails-open heuristic (see `design.md`
  D1), not a re-implementation of the devkit gate's exact mechanism.
- Wire the check into `worktrail-compile`'s step 3 (the existing scope-check
  entry point named in the brief and in `pipeline-details.md#new-pipeline`
  step 3) as a **hard gate**: a change with a newly-declared, zero-reference
  requirement fails compile before the spec PR is pushed, exactly like an
  under-scoped task already does.
- Reuse the devkit gate's non-retroactive ratchet decision (D2 in that
  change's `design.md`): enforce only requirement names newly declared by the
  current change relative to what already exists in `openspec/specs/` for the
  same capability, so a change that touches an already-merged capability with
  a pre-existing gap does not fail on it.
- Correct `devkit-requirement-coverage-gate`'s "Format Scoping" requirement
  (`openspec/specs/devkit-requirement-coverage-gate/spec.md`): its scenario
  claiming the OpenSpec path already has "equivalent coverage" from the
  existing scope-check is false and must be updated to describe the real
  state — coverage now enforced by this change's new check, not by file-scope
  inference.

## Capabilities

### New Capabilities
- `openspec-requirement-coverage-gate`: validates that every requirement an
  OpenSpec change declares in its `specs/**/spec.md` delta files is
  referenced somewhere in that change's `tasks.md`, enforced as a hard gate
  in `worktrail-compile`'s step-3 scope-check for newly-declared requirements.

### Modified Capabilities
- `devkit-requirement-coverage-gate`: correct the "Format Scoping" requirement
  scenario that currently (and incorrectly) asserts the OpenSpec path already
  has equivalent coverage via the existing scope-check.

## Impact

- **Modified**: `src/worktrail/conductor/compile.py` — `compile_run_plan`
  gains a requirement-coverage pass over the change directory's
  `specs/**/spec.md` and `tasks.md`, run before (or alongside) the existing
  file-scope compile, with its own failure surfaced through `main()`'s
  existing scope-gap error path (new error class, not reusing
  `_print_scope_gap_error`, since the failure is about requirement text, not
  task file scope).
- **New**: a coverage-parsing module (exact location decided in `design.md`)
  reading `## ADDED Requirements` / `## MODIFIED Requirements` sections and
  cross-checking `tasks.md`.
- **Modified**: `openspec/specs/devkit-requirement-coverage-gate/spec.md` —
  correct the "Format Scoping" requirement's OpenSpec scenario.
- **No change** to `pre_pr_gate.py` or `check_req_coverage.py`: this gate
  lives in the `worktrail-compile` CLI/step-3 entry point, which already runs
  before the spec PR for every OpenSpec change, not in the devkit-scoped
  pre-PR gate.
- Consuming repos whose specs use the OpenSpec format (this repo itself, and
  any repo running `worktrail` with `WORKTRAIL_SPEC_FORMAT=openspec`) gain
  the gate automatically on their next `worktrail-compile` invocation.
