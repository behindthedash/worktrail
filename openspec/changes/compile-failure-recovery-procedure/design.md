# Design

Documentation-only change to the skill bundle. The design decisions are about *what the gate
section asserts*, since the failure taxonomy has to match `worktrail-compile`'s real exit
paths or the procedure sends readers to the wrong remedy.

## D1. The taxonomy is derived from `main()`, not invented

`src/worktrail/conductor/compile.py:656` is the single source of the classes the section
lists. Read off its returns:

| Class | Source | Recovery |
|---|---|---|
| plan-shape rejection | `except PlanShapeError` (:729) | edit `tasks.md` as the problem line says (consolidate, declare disjoint scope, add the test file, retag `[cleanup]` → `[e2e]`); never a bare retry |
| scope gaps | `needs_compile(merged)` → `_print_scope_gap_error` | add `files:`, or the tail kind matching what the task executes, or `--force` with more context in `proposal.md`/`design.md` |
| unordered file collisions | `runplan.unordered_file_collisions` | add an explicit `deps` edge either direction |
| uncovered requirements | `req_coverage.find_uncovered_requirements` | add or extend a task citing the requirement |
| bad spec path / not in a repo | :694, :702 | operator error in the invocation; nothing in the change to fix |
| refused `--force` over active worktrees | `allow_force_over_active_worktrees` | fan-out is already in flight against the cached plan; do not recompile — resume or tear down that run first |
| degraded plan, **exit 0** | `compile_run_plan`'s give-up note, printed as a `note:` line | the `||` branch cannot see it; read the `note:` output |

The first four and the last are properties of the change; the middle two are properties of the
invocation. The section keeps that split explicit, because it is what decides whether the
reader edits `tasks.md` or fixes their command.

## D2. Retry is documented as the exception, not the default

The existing wording ("inspect the error above before retrying") implies retry is the shape of
recovery. For a plan-shape rejection and for uncovered requirements it is not: the compile step
is deterministic over those inputs, so an unchanged re-run reproduces the same rejection. Only
the scope-gap and collision classes have a legitimate `--force` retry, and only after adding
context the model can use. The section states this rule once rather than repeating it per row.

## D3. The gate is a section, not a script

Consistent with `#already-implemented-check`, which deliberately replaced three mechanical
probes with a read-the-source procedure: choosing between "consolidate these tasks" and
"declare disjoint file scope" is an authoring judgement. This change adds no code and no new
console script; it documents the branch and lets the enforcement test hold the shape.

## D4. Unattended mode blocks on authoring defects only

`#auto-mode-ask-fallbacks` already classifies each site as *safe default* or *finish
`blocked_product_decision`*. A compile failure over a change the run did not author is the
`#precheck-gate` case exactly -- a call about prior work -- so it blocks, quoting the compile
output, with the brief left claimed. The one class with a safe default is D1's exit-0 degrade:
the run proceeds on the baseline plan (which is what happens today regardless) and records the
degrade on the run record, so it is visible without stalling a drain pass.

## D5. Enforcement mirrors the existing structural test

`tests/test_plugin_surface.py::test_route_execution_ask_sites_carry_auto_mode_fallbacks`
already parses these files into h2 sections and asserts per-section invariants. The new test
lives beside it and reuses the same `_h2_sections` helper rather than introducing a second
parsing style: assert the anchor is inside the `#orchestrator-gates` section, assert each
taxonomy keyword appears in it, assert the banned phrase is absent bundle-wide, and assert the
citing sites name the anchor.
