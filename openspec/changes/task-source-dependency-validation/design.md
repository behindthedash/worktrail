## Context

`worktrail`'s `TaskSource` adapters (`taskformats/devkit/source.py`,
`taskformats/openspec/source.py`) each load a spec's tasks into plain dicts
carrying `deps` (same-spec dependency ids) and `external_deps` (cross-spec
`<spec-id>/<task-id>` references). `external_deps` already gets a validation
pass at load time (`validate_external_dependencies()`, devkit) and a runtime
resolution pass in `worktrail-live precheck` (`resolve_external_dependency()`).
`deps` gets neither: `coordinator.py`'s `runnable_frontier()` waits forever on
a dangling id (silent stall), while `compute_levels()`/`plan_groups()` and
`compile.py`'s `_validate()` silently drop the same dangling id from the graph
they build (inconsistent, and also silent). Devkit's optional `decision-log.md`
auxiliary file has no schema and no cross-check against tasks at all today.

## Goals / Non-Goals

**Goals:**
- Give same-spec `deps` the same load-time-adjacent validation
  `external-dependencies:` already has, surfaced through the existing
  `worktrail-live precheck` gate operators already run before `full-real`.
- Let a devkit task assert a dependency on a `decision-log.md` entry
  (`decision-refs:`) and validate that reference resolves to a decision that
  exists and is no longer open.
- Keep the check format-aware: OpenSpec tasks get the same-spec `deps` check;
  `decision-refs:` on an OpenSpec task is reported as unsupported rather than
  silently ignored, since OpenSpec changes have no `decision-log.md`
  convention to check it against.

**Non-Goals:**
- Changing `runnable_frontier()`, `compute_levels()`, `plan_groups()`, or
  `compile.py`'s `_validate()` runtime behavior. Those keep filtering
  unresolved edges the way they do today; this change adds a diagnostic gate
  ahead of them, not a different runtime dependency-resolution algorithm.
  Tightening those functions to hard-fail on an unresolved id is a materially
  larger behavior change (it can turn an existing, currently-passing spec into
  a hard failure) and is left for a follow-up if the diagnostic gate proves
  insufficient in practice.
- Defining a `decision-log.md` schema beyond the minimal `id` + status
  convention this change needs (`## D<n>: <title>` header with a `Status:`
  line). A full schema/template for `decision-log.md` is out of scope.
- OpenSpec decision tracking. OpenSpec's `design.md` already has an "Open
  Questions" section; wiring a similar dependency-coverage check to it is a
  different-shaped problem left for a future change if requested.

## Decisions

**`validate_dependencies` as a new `TaskSource` protocol method, not a
free function.** `resolve_external_dependency` already lives on the protocol
per-format because a devkit repo-relative "does this file exist" check and an
OpenSpec change-folder check differ. Same-spec `deps` validation needs no
format-specific filesystem knowledge (task ids all come from the one already-
loaded `tasks` list), so it could be a free function taking `tasks` alone —
but `decision-refs:` validation for devkit *does* need to read
`decision-log.md` from the spec directory, which only the format-specific
adapter knows how to locate. Putting both same-spec-deps and decision-refs
behind one per-format protocol method keeps the "one TaskSource, one gate
call" shape `live.py precheck()` already uses for `external_deps`, rather than
having `precheck()` call a free function for one check and a protocol method
for the other.

**`decision-refs:` frontmatter key, not overloading `deps:`.** A task
dependency on a decision is a different kind of edge than a dependency on
another task's completion — it does not participate in `runnable_frontier`'s
scheduling at all, only in the pre-launch diagnostic. Reusing `deps:` for both
would make `runnable_frontier()` either need to distinguish decision ids from
task ids (fragile string-shape guessing) or silently mis-schedule. A separate
key keeps the two concerns syntactically distinct, mirroring how
`external-dependencies:` is already its own key rather than folded into
`deps:`.

**Decision-log status convention: reuse plain prose, minimal parsing.**
`decision-log.md` has no existing schema in this repo (grep across the
codebase and `docs/specs/001-task-ac-verification-gate/` turned up zero
existing files to reverse-engineer a convention from). Rather than inventing a
rich schema nothing currently produces, the validator looks for a `## D<n>:
<title>` heading (decision id `D<n>`) and, on the following non-blank line, a
`Status: <word>` field; any status other than `decided`/`resolved` (case-
insensitive) counts as open. This mirrors the granularity `dashboard.py`
already uses for `[NEEDS CLARIFICATION]` markers — minimal, greppable, and
easy for a human or `/opsx:propose` output to produce without new tooling.

## Risks / Trade-offs

- [Risk] `decision-log.md` has no producer in this codebase today (no skill
  writes it), so the new check may have nothing to validate against in
  practice until an author adopts the convention. → Mitigation: the check is
  purely additive (WARN-only through `precheck`, same as the existing
  external-deps check) and a task with no `decision-refs:` is silently
  skipped, so this ships the *gate* without forcing adoption of the log
  format. Devkit-only scope is stated explicitly in the proposal so this
  isn't mistaken for an OpenSpec feature.
- [Risk] A spec author already has a legitimate same-spec `deps:` entry that
  happens to reference a stale id in a spec merged before this check existed.
  → Mitigation: the check is WARN-only (matches `precheck`'s existing exit-1
  contract for every other finding, not a hard block), and the existing
  `AskUserQuestion` "proceed anyway / fix / abort" gate documented at
  `#precheck-gate` already gives the operator a way through.

## Migration Plan

Additive change to `TaskSource` (new protocol method with implementations in
both adapters) and one new call site in `precheck()`. No data migration, no
existing behavior removed. Rollback is a plain revert — no persisted state
depends on the new method.
