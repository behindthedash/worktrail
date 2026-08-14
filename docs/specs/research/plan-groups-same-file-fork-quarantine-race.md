# Investigation: does `plan_groups()`'s unabsorbed same-file-fork case expose a real quarantine-orphan race?

- **Source brief:** work-queue `20260813-223607-plan-groups-s-same-file`
- **Route:** I (investigation) — no code changes
- **Date:** 2026-08-13
- **Controlling code:** `src/worktrail/orchestrator/coordinator.py::plan_groups()`
  (absorption pass added in #383), `tests/orchestrator/test_plan_groups_serial_chain_absorption.py`

## Focus

PR #383 folds a BASE task's *pure single-writer dependent chain* into BASE, closing
the datalena/#379-style quarantine-orphan race for that shape. Its own comment
(coordinator.py:317–327) documents a deliberately unabsorbed residual: when **two or
more** dependents of the same BASE predecessor each individually qualify as a pure
same-file continuation, but there is no dependency between them (a genuine fork, not
a chain), none are absorbed — there's no safe ordering to prefer between concurrent
siblings. The brief asks whether that fork shape is common enough in real specs to
matter, and if so, what the safe fix is.

## Verified Observations

1. The absorption pass only absorbs a dependent when **exactly one** candidate
   qualifies (`coordinator.py:345`, `if len(candidates) != 1: continue`). Two or more
   qualifying siblings of the same predecessor are left unabsorbed. Confirmed by
   `test_same_file_fork_absorbs_neither_sibling`.

2. **The already-covered sub-case self-heals.** When the unabsorbed siblings all
   write the *same* file as each other (as in the existing test —
   `TASK-002`/`TASK-003` both write `src/shared.ts`), the pre-existing shared-file
   union-find (`coordinator.py:373–382`, added in #25) still merges them into a
   **single** feature group. They never end up as two separate stacked groups, so
   the quarantine-orphan race does not manifest for that shape — verified by
   re-running `plan_groups()` on the test's own fixture and inspecting group
   membership.

3. **The actual exposed gap is narrower than "any fork": it requires siblings that
   write *disjoint* subsets of a multi-file predecessor.** Constructed and ran this
   case directly against `coordinator.py::plan_groups()`:

   ```python
   tasks = [
       _task("TASK-001", files=["src/shared.ts", "src/other.ts"]),
       _task("TASK-002", deps=["TASK-001"], files=["src/shared.ts"]),
       _task("TASK-003", deps=["TASK-001"], files=["src/other.ts"]),
   ]
   ```

   Result: `base=[TASK-001]`, `feature-1=[TASK-002] depends_on=[base]`,
   `feature-2=[TASK-003] depends_on=[base]` — two separate groups, neither depending
   on the other, both stacked on base. This **is** the same base-vs-dependent-group
   race #383 closed for the chain case: if `feature-1`'s PR is ready but `feature-2`
   (or base itself) is stuck in conflict resolution, the scheduler's base-before-
   dependents gate can leave a fully-done sibling idle.

4. **The brief's own parenthetical is only correct for the same-file variant.** The
   brief suggests forcing siblings + base into one group "since `runnable_frontier`
   already prevents them running in parallel due to the file collision." Checked
   `runnable_frontier()` (`coordinator.py:95–126`): it defers a task only when its
   *own* file set collides with an already-locked file. In the disjoint-subset case
   above, `TASK-002` and `TASK-003` declare **different** files and have no edge
   between them, so `runnable_frontier` would in fact let them run **concurrently**
   in the live fan-out — there is no existing collision-based serialization to lean
   on for this variant, unlike the same-file variant (which already can't run
   concurrently for the ordinary file-lock reason, independent of grouping).

5. **Empirical prevalence: 0/90 real specs.** Scanned every devkit-format spec under
   every repo in `~/projects/*/docs/specs/*/tasks/` with declared file scope (90 of
   97 spec directories found; a superset of the 81-spec sample the original
   shared-file-edge measurement in this same docstring used on 2026-07-26). For each,
   loaded tasks via `worktrail.taskformats.devkit.source.load_spec`, ran
   `plan_groups()`, and searched for the disjoint-subset-fork signature (>=2 stacked
   feature groups whose combined declared files are each a non-empty subset of a
   *single common* base predecessor's files, joined to that predecessor by a lone
   in-impl dependency edge each). **Zero matches.** The detector was verified against
   the synthetic case in point 3 above to confirm it isn't a false-negative — it
   correctly flags that case.

## Unknowns / Missing Evidence

- OpenSpec-format changes (`openspec/changes/*/tasks.md`) were **not** scanned — this
  pass only covered the legacy devkit `docs/specs/*/tasks/*.md` convention, since a
  quick generic loader across both formats + all repos was out of scope for this
  investigation. This repo (`worktrail` itself) and others are moving to OpenSpec as
  the default going forward (see this repo's own `AGENTS.md`), so the scanned corpus
  under-samples the newest specs.
- Whether LLM-inferred dependency edges (`conductor/compile.py`'s inference pass, used
  for formats without frontmatter-declared `deps`) would ever produce this shape is
  unverified. The scanned corpus is biased toward specs with *declared* file scope,
  which may also correlate with more deliberate, less ambiguous task decomposition.
- Unlike the chain case (which had a live incident — datalena run `go-20260813-194636`,
  PR #379), the fork shape has never been observed causing a real quarantine. Its risk
  is therefore currently theoretical, not reproduced from production evidence.

## Hypotheses

- **H1 — narrow combination, hence rare.** The disjoint-subset fork requires a BASE
  predecessor that itself owns >=2 files *and* >=2 dependents that each cleanly claim
  a non-overlapping subset of those files with no dependency between them. Ordinary
  task decomposition tends toward either a single continuation (now absorbed by #383)
  or dependents that diverge into genuinely new file scope (not a subset of base's
  files, so not a "continuation" at all, and already grouped independently without
  hazard). The combination needed for this specific race appears to fall between
  those two common shapes.
- **H2 — the brief's suggested fix needs one correction if pursued.** Folding base +
  all qualifying siblings into one group with an internal serialization order is a
  reasonable fix in principle, but per observation 4, the disjoint-subset variant is
  **not** already prevented from running concurrently by `runnable_frontier` the way
  the same-file variant is — a real fix would need to *additionally* impose an
  execution-order constraint inside the merged group (not just repartition PRs),
  which is a larger change than the pure same-file case #383 shipped.

## Confirmed Root Cause

Not applicable in the classic sense — this is a prevalence/risk assessment of an
already-documented, deliberately-scoped residual gap, not a reproduced defect. The gap
itself (disjoint-file-subset multi-dependent forks of a multi-file base task) is
precisely characterized and its behavior is verified (observations 1–4). Its
real-world occurrence rate, however, is confirmed empirically at zero across the
sampled corpus (observation 5) — not merely assumed.

## Recommendation

**Leave as documented residual risk; do not implement a fix now.** Rationale:

- 0/90 real specs exhibit the shape, against a corpus larger than the one that
  justified the *previous* related change (#25's shared-file edges, based on 81
  specs).
- A correct fix is more involved than the brief's own suggested approach implies
  (H2) — it would need in-group execution ordering, not just PR repartitioning,
  since `runnable_frontier` does not already serialize the disjoint-subset variant.
- The existing docstring in `coordinator.py` (lines 317–327) already accurately
  documents this exact residual and its rationale, so the risk is not silently
  unrecorded — a future occurrence has a clear pointer back to this analysis.

If the shape is ever observed live (a future incident, or a later re-scan across a
larger/OpenSpec-inclusive corpus turns up real instances), capture the concrete task
shape as a new regression test alongside `test_same_file_fork_absorbs_neither_sibling`
before implementing H2's fix — do not speculatively build the in-group serialization
mechanism against a shape with zero observed occurrences.

**Not continuing into Route F** — no code defect to fix; recommendation is to accept
the documented risk as-is.
