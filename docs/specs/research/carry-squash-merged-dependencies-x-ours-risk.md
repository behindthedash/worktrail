# `_carry_squash_merged_dependencies`'s unconditioned `-X ours` merge can silently discard live-base content outside the triggering dependency's own files

Investigation of work-queue brief `20260816-153112`. `_carry_squash_merged_dependencies`
(`src/worktrail/orchestrator/live.py:1572-1623`, called from `add_stacked_worktree`) carries
a DONE-but-branch-gone dependency's content into a stacked task worktree by merging the
freshly-fetched live base ref with `git merge --no-edit -X ours <ref>`, with no verification
of what the `-X ours` strategy actually resolved. This is the same architectural risk class
root-caused and fixed for `integrate_one`'s dependency-branch-gone fallback (PR #475,
`docs/specs/research/integrate-one-dep-branch-gone-fallback-root-cause.md`), which that
investigation flagged as deferred, out-of-scope follow-up work for this exact function.

## Verified Observations

- `_carry_squash_merged_dependencies` triggers only for a dependency that is `DONE`-like
  (`coordinator.DONE`), whose task branch no longer exists (`_branch_exists` false), and that
  has at least one declared file missing from the stacked worktree
  (`_dependency_file_declared_path_exists` false) — a narrow, well-scoped trigger condition.
- Once triggered, it fetches the freshest base ref (`<remote>/<base>`, falling back to local
  `<base>`) and merges it into the worktree with `git merge --no-edit -X ours <ref>`
  (`live.py:1614`). `-X ours` auto-resolves every content-level conflict across the ENTIRE
  merge in favor of the worktree's side — it is not scoped to the triggering dependency's own
  declared files, and it does not fail loud: a conflicting hunk is silently resolved, never
  reported.
- At the point this merge runs, the worktree's HEAD (`dependency_start_ref`, `live.py:1424-1461`)
  is either a still-live sibling dependency's branch tip, or — for exactly the branch-gone case
  that triggers this function — a bare fallback to the local `HEAD`, which the function's own
  docstring states explicitly "predates those merges" (the dependency's actual squash-merge onto
  the live base). This stale start point is exactly the same "reconstructed, possibly stale"
  shape the `integrate_one` investigation found unsafe to reconcile via unconditioned `-X ours`.
- Reproduced empirically in this run (`tests/orchestrator/test_stacked_worktree_squash_carry.py`,
  `CarrySquashMergedDependenciesContentLoss`): a scenario where the live base's squash-merge
  changes a shared file's content (not one of the dependency's declared files) while the local
  checkout independently diverged on the same file/line. Running the current code: the
  dependency's own declared file (`dep_file.py`) DOES land correctly — the narrow trigger
  condition behaves as documented — but the unrelated shared file silently keeps the worktree's
  stale content and the live base's newer content on that file is discarded with no error, no
  warning, and no conflict markers. The merge reports success.
- `add_stacked_worktree`'s sibling-dependency merge step, immediately before this carry runs
  (`live.py:1683-1703`), treats a conflicted merge as a hard failure
  (`WorktreeStackConflictError`) rather than auto-resolving — the surrounding code already
  treats "the two sides might not be identical" as something that must fail loud, everywhere
  except this one `-X ours` call.
- The function's own docstring already anticipates this failure mode should surface: "A merge
  failure aborts and falls through with a WARN: `_require_dependency_files` stays the fail-loud
  backstop when the content genuinely isn't available." That backstop only engages if the merge
  actually fails (non-zero exit) — `-X ours` prevents that by design, since it auto-resolves
  instead of leaving a real conflict.
- The owning spec (`openspec/specs/stacked-worktree-conflict-resolution/spec.md`, "Carry
  already-merged dependency content across a squash-merge boundary") justifies `-X ours` as
  "consistent with the existing group-branch squash reconciliation," citing `integrate_one`'s
  mechanism. That mechanism was removed by PR #475 specifically because the byte-identical
  assumption it relied on does not hold in general — the spec's rationale now cites a removed
  approach.
- No test in `tests/` called `add_stacked_worktree` with `remote`/`base` arguments before this
  run — the carry path (reachable from real orchestrator runs via `live.py`'s `ensure_wt`) had
  zero coverage, not even a happy-path test.

## Unknowns / Missing Evidence

- No observed production incident from this specific function (unlike the `integrate_one` case,
  which had a real incident, PR #414). This audit finding is preemptive: the prior investigation
  flagged it as "speculative (no observed incident)" and deliberately deferred it rather than
  filing it as a bug.
- Whether any current orchestrator run has actually hit the narrow trigger condition (DONE
  dependency, branch gone, missing declared file) in a way that also touched a shared file on
  both sides cannot be determined from the codebase alone — no incident log entry exists to
  check against.

## Hypotheses

None remaining as open hypotheses — see Confirmed Root Cause below. The repro test converts
what was an architectural-risk hypothesis into an empirically observed defect.

## Validation Steps

- `tests/orchestrator/test_stacked_worktree_squash_carry.py` (added this run) reproduces the
  conflicting-content scenario directly: a live-base squash-merge and a diverged local checkout
  both touch the same shared file/line, with the dependency's own declared file present only on
  the live-base side. Asserts the dependency's file carries correctly (narrow trigger condition
  intact) while documenting that the shared file's live-base content is silently discarded.

## Confirmed Root Cause

Confirmed by code inspection and by the regression test added in this run:
`_carry_squash_merged_dependencies` reconciles a stale, reconstructed worktree start point
against the live base with an unconditioned `-X ours` merge, exactly the same mechanism
`integrate_one`'s dependency-branch-gone fallback used before PR #475 removed it as unsafe. The
blast radius is not scoped to the triggering dependency's own declared files — any file both
sides happen to touch is resolved silently in favor of the (possibly stale) worktree content,
with no failure signal. This matches the brief's finding exactly.

## Recommended Fix (continued inline as Route F)

Drop `-X ours` from the carry merge (`live.py:1614`) so a genuine content-level conflict
surfaces as a real conflicted merge instead of being silently auto-resolved. The existing
surrounding machinery already treats that as the correct failure path: a failed merge aborts
and prints a `WARN`, and the fail-loud `_require_dependency_files` backstop downstream still
catches a worktree that ends up missing the dependency's declared files. Removing `-X ours`
does not change behavior for the common case (no real conflict, the merge fast-forwards or
merges cleanly); it only changes what happens when the two sides genuinely diverge on the same
content — from "silently pick the stale side" to "fail loud, same as every other merge in this
code path." See the accompanying PR for the diff, the updated test, and the corrected spec
rationale text.

## Deferred Work

None — the fix is small, clearly in scope, and continues in this same run per Route I's
continuation rule.
