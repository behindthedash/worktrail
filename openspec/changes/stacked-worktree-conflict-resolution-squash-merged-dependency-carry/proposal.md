# Proposal: Carry squash-merged dependency content into stacked worktrees

## Why

`add_stacked_worktree` fails to carry a dependency's declared files across a squash-merge
boundary: when a dependency's group PR is squash-merged and its task branch deleted
(`verify.cleanup_group`) before the dependent task's worktree is created — deterministic for
tail e2e/cleanup tasks, which `_dispatch_pending_tail` only dispatches after every group is
merged and cleaned up — `dependency_start_ref` falls back to the run-start local base, which
predates those merges. The worktree lacks the dependency's content, and
`_require_dependency_files` raises `WorktreeMissingDependencyFileError` (live.py:2312): the
dependency's journaled `head_sha` is no longer an ancestor of anything post-squash, and the
DONE-status downgrade only engages when no `head_sha` is available. This forced a manual
`worktrail-live skip` + hand-verification during the task-source-dependency-validation run
(2026-08-09), breaking the v1.0 release-gate clean-run streak (brief
20260809-020908, blocker; duplicate capture 20260809-020855).

## What Changes

- `add_stacked_worktree` gains a post-stack carry step: when a dependency in a DONE-like
  status has no task branch left (merged + cleaned up) and one of its declared files is
  missing from the freshly stacked worktree, fetch the base ref and merge the freshest
  available base (`<remote>/<base>`, falling back to local `<base>`) into the worktree with
  `-X ours` (the same squash-reconcile strategy `integrate.py` already uses for group
  branches), so the worktree actually contains the squash-merged dependency content.
- `remote`/`base` are threaded to `add_stacked_worktree` as optional parameters from both
  schedulers (`live_run_real` sequential path via `_dispatch_pending_tail`/`_full_real_inner`,
  and `_pipeline_scheduler`); when absent (cassette/demo path, legacy callers), behavior is
  unchanged.
- The lifecycle harness's fake `gh pr merge` performs a real squash merge (matching the
  merge methods its `repo view` already advertises: squash-only), and a new harness scenario
  covers a spec with a tail e2e task whose dependencies are squash-merged and cleaned up
  before tail dispatch.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `stacked-worktree-conflict-resolution`: adds a requirement that a stacked worktree carries
  the content of dependencies that were already merged into base and had their branches
  deleted (squash-merge boundary), instead of failing dependency-file validation at dispatch.

## Impact

- `src/worktrail/orchestrator/live.py`: `add_stacked_worktree` (carry step + optional
  `remote`/`base` params), `live_run_real` (threading), `_dispatch_pending_tail` (threading),
  `_pipeline_scheduler._ensure_wt` (threading), sequential `ensure_wt` (threading).
- `tests/orchestrator/lifecycle/fake_gh.py`: `pr merge` becomes a real squash merge.
- `tests/orchestrator/lifecycle/test_lifecycle_harness.py`: new squash-merged-dependency
  tail-dispatch scenario.
- No CLI surface changes; no journal format changes. `_require_dependency_files` semantics
  unchanged (still fail-loud when content is genuinely missing).
