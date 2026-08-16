## Why

Task generation for an OpenSpec change can collapse a wide, parallelizable DAG into a serial
chain when several tasks across different phases all need to touch the same "hot" file. Observed
live on the datalena `097` run: `--max-workers 5` sat mostly idle after tick 2 because hot files
(`app-shell.tsx`, `nav-registry.ts`) were each written by a task in nearly every phase, so
`plan_groups()`'s shared-file union (`docs/design/conductor-lanes.md` §4.2, P1) folded the whole
DAG into one lane. The grouping fix already shipped (worktrail PR #383, "fold same-file serial
dependent chains into base group") makes that folding *safe* once it happens, but it does not
stop tasks.md authoring from *creating* the wide fan-in in the first place. The root cause is
upstream of grouping: `tasks.md` generation guidance (in the `openspec-propose` skill's
tasks-artifact step) has no instruction to spread ownership of a file that recurs across phases,
so an LLM authoring tasks naturally assigns "add a nav entry" to whichever task's phase needs it,
independently per phase, with no incentive to avoid repeat ownership of the same file.

## What Changes

- Add file-ownership-bias guidance to the `openspec-propose` skill's tasks-artifact authoring
  step (`skills/openspec-propose/SKILL.md`): when authoring `tasks.md`, bias task decomposition so
  that a file expected to be touched across more than one phase is owned by at most one task per
  phase, splitting an otherwise-repeated same-file edit (e.g. registry/data-table additions) into
  separate per-phase files that a single later task composes/merges, rather than having every
  phase's task rewrite the same shared file directly.
- Keep the existing collision-serialization behavior (shared-file edges still union into one lane
  per `docs/design/conductor-lanes.md` §4.2) for any hot-file collision the guidance cannot avoid
  — this is generation-time guidance to reduce how often that folding is *needed*, not a
  replacement for it.
- Document the split rationale in `docs/design/conductor-lanes.md` so the generation-time bias and
  the grouping-time fold (§4.2) are cross-referenced as two complementary mitigations for the same
  wide-fan-in-collapses-to-chain failure mode, rather than reading as redundant fixes.

## Capabilities

### New Capabilities
- `task-generation-file-ownership-guidance`: guidance requirements governing how the
  `openspec-propose` skill's tasks-artifact authoring step biases per-phase file ownership to
  avoid unnecessary same-file serialization across an OpenSpec change's task DAG.

### Modified Capabilities
(none — no existing capability governs `openspec-propose`'s tasks-artifact authoring content;
the closest sibling, `openspec-requirement-coverage-gate`, governs a different, code-enforced
concern (requirement-name coverage), not file-ownership bias)

## Impact

- `skills/openspec-propose/SKILL.md` — tasks-artifact authoring step gains a new guidance
  sub-bullet (prose only, no schema/code change; mirrors how the existing file-less-task and
  requirement-coverage guidance already live in this same step).
- `docs/design/conductor-lanes.md` — §4.2 gains a cross-reference note; no design decision this
  doc already recorded is reversed or superseded.
- No changes to `src/worktrail/conductor/`, `src/worktrail/orchestrator/`, or any executable code
  path: this is authoring-time guidance consumed by the LLM writing `tasks.md`, not a new
  mechanical check. `worktrail-compile`'s existing scope-check and the shipped P1 grouping fold
  remain the deterministic backstops for whatever the guidance does not fully prevent.
