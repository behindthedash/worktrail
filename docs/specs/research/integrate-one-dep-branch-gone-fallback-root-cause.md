# `integrate_one`'s dependency-branch-gone fallback reintroduces duplicate content and can revert live-base changes

Investigation of work-queue brief `20260815-010310`. `integrate_one`'s (`src/worktrail/orchestrator/integrate.py`)
dependency-branch-gone fallback reintroduced already-shipped duplicate code and reverted
`tasks.md` checkboxes when PR #414 (concurrent-drain-workers feature-1) hit it after PR #413
fixed a sibling bug. PR #418 corrected the live damage but did not change the fallback logic
itself.

## Verified Observations

- The fallback lives at `integrate.py:995-1025` (current `main`, commit `fca42b6`). It fires
  whenever a dependent group's `target` (its dependency's recorded `group_branch[dep]` value)
  fails `git rev-parse --verify` — i.e. the dependency's branch ref does not exist.
- Two distinct situations reach this same fallback with the same `target != base` precondition
  (`integrate.py:989`):
  1. **Real branch, deleted after squash-merge.** The dependency group actually built and
     pushed a real branch (`f"{run_id}/{name}"`) earlier in *this* run, it was squash-merged,
     and normal cleanup deleted it.
  2. **Synthetic marker, never a real ref.** The dependency's tasks were all already
     `ALREADY_INTEGRATED` (status `"completed"`) from a **prior, separate run** —
     `integrate.py:963-972`. No branch is ever built in this case; `group_branch[name]` is set
     to the literal string `f"{run_id}/{name}"` purely as a bookkeeping placeholder so
     dependents don't cascade-quarantine. This is the path the brief names explicitly ("the
     'MERGED (all tasks already integrated from prior run)' implicit-merge branch").
- Both situations produce a `target` string that fails `rev-parse --verify` and enter the exact
  same fallback code with no distinction between them.
- The fallback (after fetching the remote base fresh, `integrate.py:1009`) computes
  `mb = merge-base(first_deliverable_task_branch, origin/<base>)` and uses `mb` — a **historical
  commit that can predate the dependency's actual squash-merge** — as the integration
  worktree's start point (`integrate.py:1012-1017`). It records `squash_reconcile_ref =
  origin/<base>` for later reconciliation.
- Later (`integrate.py:1080-1081`), after merging the dependent group's own deliverable task
  branches onto that stale start point, the code merges `squash_reconcile_ref` back in with
  `git merge --no-edit -X ours <squash_reconcile_ref>` — **with no return-code check and no
  verification of what actually got resolved**. `-X ours` auto-resolves every content-level
  conflict in favor of the (stale) worktree side; it does not fail loud, it silently keeps the
  worktree's version wherever the two sides touch the same lines.
- The governing comment (`integrate.py:1076-1079`) states the assumption explicitly: "the task
  branches carry the base group's content from before the squash; `-X ours` resolves apparent
  conflicts in our favor (content is byte-identical)." That assumption holds for situation 1
  (branches from the same run, same lineage, squashed once) but **does not hold for situation
  2**: a dependency verified `ALREADY_INTEGRATED` from a prior, separate run has no guaranteed
  shared lineage with the dependent group's task branches, and nothing in the code checks that
  the two sides are actually byte-identical duplicates before trusting `-X ours`.
- `_write_group_task_status` (`integrate.py:284-329`), which stamps this group's own tasks
  `completed` in the spec artifact, is scoped strictly to `group["tasks"]` and runs *after* the
  reconcile merge. It cannot itself explain a checkbox revert for a *different* (dependency)
  group's tasks — the revert has to come from the reconcile merge discarding the dependency's
  already-landed checkbox edits on `origin/<base>` wherever they land on the same lines/region
  the dependent's stale worktree touches (a single shared `tasks.md` — this repo's own OpenSpec
  format uses exactly one `tasks.md` per change — makes such overlap likely).
- Existing test coverage (`tests/orchestrator/test_dep_group_integrate.py`,
  `DeletedDepBranchFallback`) exercises only situation 1 (a `group_branch` entry seeded with a
  real-looking branch name that "was deleted"), asserting the merge-base + `-X ours` behavior as
  correct. No test exercises situation 2 (the synthetic `ALREADY_INTEGRATED` marker reaching
  this fallback), and no test asserts anything about the reconcile merge's outcome on shared
  files beyond "the command was called."
- A separate, spec-owned function — `add_stacked_worktree()` in `worktree.py`, governed by
  `openspec/specs/stacked-worktree-conflict-resolution/spec.md`'s "Carry already-merged
  dependency content across a squash-merge boundary" requirement — deliberately documents the
  same `-X ours`-favors-stacked-content pattern as intended behavior for *that* function. This
  investigation and its fix touch only `integrate_one`; `add_stacked_worktree()` is a different
  component serving a different purpose (worktree creation for a not-yet-integrated task, not
  building a dependent group's PR branch) and carries its own spec — changing it is a different
  purpose than this brief and is out of scope here. Flagged as a related risk below (Deferred
  Work), not fixed inline.

## Unknowns / Missing Evidence

- Whether the exact PR #414 incident manifested via situation 2 (implicit-merge marker) or
  situation 1 (a real branch deleted between runs) cannot be re-derived from the current
  `main` tree alone — the incident's own branches/PRs are gone. The brief's own account
  explicitly names the implicit-merge code path, which is treated here as the authoritative
  description of what happened, but this write-up cannot independently replay the exact commit
  sequence.
- No direct evidence (e.g. a saved PR diff) of the specific reverted `tasks.md` lines from the
  #414 incident — the mechanism above is inferred from code structure and the `-X ours`
  semantics, not observed on the actual incident diff.

## Hypotheses

- **Root cause (high confidence, code-verified mechanism):** the fallback conflates two
  different situations — a dependency that finished *within this run* (real branch, later
  deleted) and a dependency verified merged in a *prior, separate run* (never a real branch at
  all) — under one heuristic that reconstructs a historical, possibly stale, start point and
  then blindly favors that stale content on any conflict against the live base. When the
  dependency's actual content on the live base has moved in a way the stale point doesn't
  share (e.g. the dependency's own later `tasks.md` stamp), `-X ours` silently discards it.

## Validation Steps

- Added `tests/orchestrator/test_dep_group_integrate.py::ImplicitMergeMarkerFallback` (this PR)
  reproducing situation 2 directly: `group_branch["base"]` seeded with the synthetic
  `f"{run_id}/base"` marker (never created as a real ref, matching `integrate.py:968`) and
  asserting the pre-fix code path (merge-base + unconditioned `-X ours` reconcile, no
  verification) — see the PR for the exact assertions and the fix that replaces this fallback
  path with a direct, live-base target.

## Confirmed Root Cause

Confirmed by code inspection and by the regression test added in this run: `integrate_one`'s
dependency-branch-gone fallback (`integrate.py:995-1025,1076-1081`) resolves to a reconstructed,
potentially stale historical commit and reconciles divergence from the live base with an
unconditioned `-X ours` merge, instead of resolving directly to the dependency's actual,
already-confirmed-merged position on the live base. This matches the brief's description and
explains both symptoms (reintroduced duplicate code, reverted `tasks.md` checkboxes) as the same
underlying mechanism: `-X ours` discarding live-base content that the stale reconstructed start
point never possessed.

## Recommended Fix (continued inline as Route F)

Both situations that reach this fallback share one guaranteed fact: by the time
`rev-parse --verify` fails on `target`, the dependency's content is **already fully present** on
the live base (that's *why* its branch is gone — either squashed-and-cleaned-up in this run, or
verified `ALREADY_INTEGRATED` from a prior run). The fix removes the stale
merge-base-reconstruction-plus-reconcile dance and instead points `target` directly at the
freshly-fetched live base ref (`origin/<base>`) — the same kind of live ref an independent
group's `target` already uses by default (`integrate.py:989`). Deliverable task branches then
merge onto that live target through git's normal 3-way merge, which finds the true common
ancestor and surfaces any genuine conflict instead of silently discarding one side. See the
accompanying PR for the diff and updated/added tests.

## Deferred Work

- `add_stacked_worktree()`'s spec-documented `-X ours` reconciliation
  (`openspec/specs/stacked-worktree-conflict-resolution/spec.md`) shares the same
  byte-identical-content assumption this investigation found unsafe for `integrate_one`. It is a
  different function/spec and out of scope for this fix, but worth a dedicated look — not filed
  as a handoff brief yet since it is speculative (no observed incident), left as a note here for
  a future investigation to pick up if warranted.
