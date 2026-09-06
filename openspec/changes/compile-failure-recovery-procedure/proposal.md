## Why

`worktrail-compile` is a pre-launch gate: `#orchestrator` runs it against every OpenSpec
change before `full-real`, and both `pipeline-details.md` scope-check steps run it before a
spec PR is pushed. It is the only gate of the three whose failure handling is undocumented.

`grep -rn "inspect the error above before retrying" skills/` returns exactly one site --
`skills/worktrail-go/references/subagent-prompts.md:750`:

```
worktrail-compile "$SPEC_ROOT/openspec/changes/$SPEC_ID" || {
  echo "ERROR: worktrail-compile failed for $SPEC_ID — inspect the error above before retrying full-real." >&2
  exit 1
}
```

That is the whole procedure: a bare `echo`/`exit 1` telling the reader to inspect output the
prose never explains. Its two sibling gates in the same file each have a dedicated section
under `#orchestrator-gates` -- `#already-implemented-check` (:1733) and `#precheck-gate`
(:1770) -- with a branch-by-branch procedure, an `AskUserQuestion` menu, and an
`$AUTO_MODE=true` fallback, plus a per-site policy line in `#auto-mode-ask-fallbacks` (:350).
The compile gate has none of the three.

The consequences are concrete, because `worktrail-compile`'s non-zero exits are *not* one
condition. `main()` (`src/worktrail/conductor/compile.py:656`) exits 1 for six distinct
reasons that need six different actions:

- a `PlanShapeError` (:729) -- serial chain, same-file dependent chain, implementation task
  with no test scope, verification-bodied `[cleanup]` task -- which is an authoring defect in
  `tasks.md` that re-running compile can never resolve;
- scope gaps (`_print_scope_gap_error`) -- add `files:`, or a tail kind, or recompile
  `--force` with more context in `proposal.md`/`design.md`;
- unordered file collisions -- add a `deps` edge;
- uncovered requirements -- add a task covering the requirement;
- a bad spec path or a directory outside a git repo (:694, :702) -- an operator/path error,
  nothing to fix in the change;
- a refused `--force` over existing task worktrees -- fan-out is already in flight.

And one failure mode that exits **0**: a compile whose worker never answers degrades to the
baseline plan with a `note:` line, which the `||` branch above cannot see at all -- the run
proceeds fully serialised and the gap only surfaces later, when `validate_task_metadata()`
refuses to fan those tasks out.

With no documented mapping, an agent that hits any of these has one lever the prose gives it
("retry"), which is wrong for four of the six exit-1 paths and cannot be reached for the
exit-0 one. Unattended, there is no `$AUTO_MODE` policy line at all, so a drain run's
behaviour on a compile failure is whatever the executing model improvises.

## What Changes

- **A dedicated `#compile-gate` section** under `#orchestrator-gates`, sibling to
  `#already-implemented-check` and `#precheck-gate`, carrying a failure-class → recovery-action
  table covering every non-zero exit path plus the silent degraded-plan case, and stating the
  one rule that distinguishes them: a plan-shape or coverage rejection is an authoring defect
  that a re-run cannot fix, so re-running compile unchanged is never the response to it.
- **The `#orchestrator` code block cites the section** instead of saying "inspect the error
  above": the `||` branch points at `#compile-gate` by anchor, and additionally checks the
  degraded-plan `note:` on a zero exit, which today nothing reads.
- **An `$AUTO_MODE=true` fallback**, in-section and as a per-site bullet in
  `#auto-mode-ask-fallbacks`: an authoring defect in a change an unattended run did not write
  is not a call it may make, so it finishes `blocked_product_decision` quoting the compile
  output with the brief left claimed -- while the classes with a safe documented default (a
  degraded plan from an unanswered compile worker) take it and record it.
- **Both `pipeline-details.md` scope-check steps cite the same anchor**, so the `new` and
  `implement` pipelines get the same recovery procedure instead of their own one-line echo.
- **An enforcement test** pins the gate's structure the way
  `test_route_execution_ask_sites_carry_auto_mode_fallbacks` pins the others': the anchor
  exists under the gates section, every documented compile failure class appears in it, the
  bare "inspect the error above" wording cannot return, and the citing sites resolve.

## Capabilities

### Added Capabilities

- `compile-gate-recovery`: the compile pre-launch gate's documented failure taxonomy, per-class
  recovery actions, unattended fallback, and the enforcement that keeps them present.

## Impact

- **Docs**: `skills/worktrail-go/references/subagent-prompts.md` (new `#compile-gate` section,
  the `#orchestrator` code block, the `#auto-mode-ask-fallbacks` per-site list),
  `skills/worktrail-sdd-workflow/references/pipeline-details.md` (the `new` and `implement`
  scope-check steps).
- **Tests**: `tests/test_plugin_surface.py` (new structural test).
- **Non-goals**: changing `worktrail-compile`'s behaviour, exit codes, or diagnostics; adding
  automatic retry or auto-repair of a rejected plan (`runplan-collision-auto-repair` owns that
  ground); changing when the gate runs or making it blocking where it is not today; the other
  two gates' procedures.
