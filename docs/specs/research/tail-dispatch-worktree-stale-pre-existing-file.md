# Investigation: tail-kind task worktrees stay stale when a dependency edits a pre-existing file

Route I investigation, brief `20260817-120332-full-real-s-tail-kind`. Continued
into Route F in the same run (root cause confirmed, fix is small and clearly
in scope).

## Verified Observations

- `full-real`'s tail dispatch (`_dispatch_pending_tail`, `live.py:3488`) runs
  after `integrate_complete` — i.e. after every non-tail group has been
  integrated, verified, merged, and cleaned up (`verify.cleanup_group`
  deletes the task branch). It calls `live_run_real(..., with_tail=True,
  resume=True, ...)` directly.
- `_refresh_base_branch` (`live.py:1232`), which fetches `<remote>/<base>`
  and fast-forwards the LOCAL `base` ref, is called exactly once, at the top
  of `_full_real_inner` (`live.py:4624`) — **before** the fan-out begins.
  There is no second call between group-merge completion and
  `_dispatch_pending_tail`.
- Live evidence from run `ci-watch-loop-graphql-outage-fallback`
  (`~/projects/worktrail-worktrees/ci-watch-loop-graphql-outage-fallback-spec-worktrees/run-ci-watch-loop-graphql-outage-fallback.json`):
  task `3.1` (`[cleanup]` tail kind, `deps: ["2.4"]`, `files: []`) failed with
  `context_quality: "insufficient"` and
  `missing_context: ["skills/worktrail-go/references/ci-watch-loop.md has no
  'GraphQL outage fallback' subsection ... file's last change is #500, only
  the spec-proposal commit e8a1f93 is on top"]` — i.e. the worktree never
  received the content merged via PRs #504/#505.
- The compiled RunPlan for that run
  (`~/projects/worktrail-worktrees/runplans/ci-watch-loop-graphql-outage-fallback-*.json`)
  shows task `2.4` (3.1's only dependency) DOES declare
  `files: ["skills/worktrail-go/references/ci-watch-loop.md"]` — so the
  freshness-carry mechanism's file-scope precondition was met.
- `add_stacked_worktree`'s post-stack carry, `_carry_squash_merged_dependencies`
  (`live.py:1574`), only fetches/merges the fresh base ref for a dependency
  when `dep.get("status") in DONE`, its task branch is gone (squash-merged),
  **and** `any(not _dependency_file_declared_path_exists(wt, f) for f in
  dep.get("files", []))` — i.e. only when at least one of the dependency's
  declared files is *missing* from the worktree.
- `_dependency_file_declared_path_exists` (`live.py:2540`) is a plain
  existence check: `(wt / declared).exists()` (or a glob match for
  wildcard entries). It has no notion of content freshness.
- `skills/worktrail-go/references/ci-watch-loop.md` is a long-lived file that
  already existed in the repo before this change (last touched by PR #500).
  So `_dependency_file_declared_path_exists(wt, "skills/worktrail-go/references/ci-watch-loop.md")`
  is `True` regardless of which commit `wt` was forked from — the path
  exists either way. `stale_deps` therefore never includes `2.4`, the carry
  never fires, and `wt` stays pinned to its original stale start point
  (the pre-fan-out spec-proposal commit).
- The existing regression coverage for this carry
  (`tests/orchestrator/test_stacked_worktree_squash_carry.py`) only exercises
  a dependency whose declared file is **newly created** (`dep_file.py`,
  which does not exist before the squash-merge) — the exact case where the
  existence check happens to work. Neither test constructs a dependency that
  edits a file already present in the base checkout, so this gap had zero
  coverage.

## Unknowns / Missing Evidence

- Whether any other caller of `_dependency_file_declared_path_exists` relies
  on its existence-only semantics in a way a stricter check would break
  (checked: only other caller is `_require_dependency_files`, the fail-loud
  backstop that raises when a declared file is missing after the carry
  attempt — its existence-only semantics are correct there, since it runs
  *after* the carry and is asking "did the content ever arrive," not
  "is this worktree fresh").

## Hypotheses (confirmed below)

- H1: the file-existence precondition in `_carry_squash_merged_dependencies`'s
  `stale_deps` filter is a broken proxy for "worktree lacks the dependency's
  content" whenever the dependency's declared file already existed in the
  repo before the dependency's own change — which is the common case for any
  edit to an established file (docs, orchestrator source, etc.), not just
  this one change.

## Confirmed Root Cause

`_carry_squash_merged_dependencies`'s `stale_deps` filter gates the
fetch-and-merge carry on `_dependency_file_declared_path_exists`, a bare
`Path.exists()`/glob check. For a dependency whose declared file already
existed in the repo before the dependency's change (true for edits to any
established file — the tail-verification scenario reported live is not
change-specific), the path trivially exists in every worktree regardless of
which commit it was forked from, so the check can never detect staleness.
The dependency is never added to `stale_deps`, the carry's `git fetch` +
`merge-base --is-ancestor` + `git merge` sequence never runs, and the
worktree is permanently pinned to its pre-fan-out start point. This
reproduces exactly the live failure on task 3.1 of
`ci-watch-loop-graphql-outage-fallback` (missing context flag citing PR #500
instead of the just-merged #504/#505), and is a general risk for any
OpenSpec change whose tail-kind task depends on edits to a pre-existing file
— not specific to that one change.

## Recommended Next Route

Route F (defect repair) — continued in this run. Fix: stop gating the carry
on the broken existence heuristic; rely on the already-present, exact
`merge-base --is-ancestor` check (right after the fetch) to short-circuit
when nothing is actually missing, instead of a heuristic pre-filter that
can't tell a stale copy from a fresh one when the path merely exists.
