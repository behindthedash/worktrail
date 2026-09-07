## Why

`~/.worktrail/detached/orch-worktrail-shape-gate-pending-tasks-only.log` shows a real, live
occurrence of both halves of this defect on the same day. `PR #973` archived (deleted from
`main`) the unrelated sibling change directory `openspec/changes/cleanup-tail-task-verification-guard/`.
`feature-1`'s group PR (`PR #970`) then went `CONFLICTING` against that advanced base, and
`ensure_mergeable()` spawned a `dispatch.ROLE_RESOLVE` worker to fix it. `build_group_prompt()`'s
resolve instructions (`src/worktrail/orchestrator/dispatch.py:826-829`) tell the worker to
"preserve the intent of BOTH sides (the base advanced; keep your group's changes and the base's)"
with no exception for a path the base *deleted* — so the worker resurrected the archived sibling
change's files (`design.md`, `proposal.md`, `tasks.md`) back onto `feature-1`'s branch, well
outside anything `feature-1`'s own tasks declared.

The deny-list check at `src/worktrail/orchestrator/verify.py:894-901`
(`_forbidden_paths_touched`, `openspec/` is one of `FORBIDDEN_WORKER_PATH_PREFIXES`) did catch
this and logged it as a strike failure at line 897. But `ensure_mergeable()` returning `False`
only aborts *this run's own* attempt to drive the PR to merged — it does not revert the resolve
worker's already-pushed commit, and it does not tell `gh` anything. `verify_one()`
(`verify.py:1792-1799`) then falls through to `_recheck_merged_before_quarantine()`, whose only
job is to keep a group that merged out from under a bounded poll/strike budget from being
mis-recorded as quarantined — it checks nothing except live PR state (`quarantine-live-merge-recheck`
capability). This repo's own external "CI: Auto-merge on open" automation had already merged the
resolve worker's pushed commit (conflict resolution done, checks green) independent of the
orchestrator's own gate, so the recheck saw `state=MERGED` and reported the group as cleanly
`merged` — silently discarding the forbidden-path finding it had itself logged one call earlier.
`gh pr view` on `PR #970` confirms it merged. The recheck already has exactly this exclusion for
a confirmed self-merge violation (`verify.py`'s `_self_merge_violations` / `violation` guard at
line 1793/1797); a confirmed forbidden-path violation has no equivalent, so it takes the same
"already merged, don't quarantine" exit a legitimate late external merge does.

No existing candidate covers this: `shared-pr-landing-pipeline` is about PR landing/CI gating
mechanics, not merge-conflict resolve-worker scope enforcement, and does not name
`dispatch.py`'s resolve prompt or `verify.py`'s recheck/forbidden-path interaction.

## What Changes

- `build_group_prompt()`'s `ROLE_RESOLVE` conflict-resolution instructions gain an explicit rule:
  a path the base branch deleted stays deleted — the worker MUST NOT resurrect it to "preserve
  both sides," particularly a path outside the group's own declared task scope (e.g. another
  OpenSpec/devkit change directory). This is the root-cause fix: it removes the ambiguous
  instruction that led the worker to recreate archived files in the first place.
- `_spawn_group_worker()` in `verify.py` records a confirmed forbidden-path violation the same
  way `_detect_self_merge()` already records a self-merge violation — a per-group dict keyed by
  group name, checked by `verify_one()` alongside the existing self-merge `violation` guard so
  `_recheck_merged_before_quarantine()` never runs for a group with a confirmed forbidden-path
  violation, regardless of the PR's live merge state.
- `run_all()`'s result dict gains a `forbidden_path_violations` bucket (mirroring `self_merged`)
  so a resolve/ci-fix worker that got a forbidden-path finding merged out from under the check is
  surfaced distinctly from an ordinary `quarantined` group and from a clean `merged` one — never
  silently dropped.

## Capabilities

### Added Capabilities

- `resolve-worker-scope-discipline`: the resolve worker's conflict-resolution instructions
  explicitly forbid resurrecting base-deleted paths outside the group's own scope, and a
  confirmed forbidden-path violation by a resolve/ci-fix worker is tracked and surfaced as its
  own distinct outcome bucket rather than folded into an ordinary quarantine.

### Modified Capabilities

- `quarantine-live-merge-recheck`: the live merge recheck's existing self-merge/post-merge-
  regression exclusion is extended to also exclude a group with a confirmed forbidden-path
  violation — a group already carrying that stronger, independently-verified finding is never
  overwritten by "the PR is MERGED, so don't quarantine."

## Impact

- **Code**: `src/worktrail/orchestrator/dispatch.py` (`build_group_prompt`, `ROLE_RESOLVE`
  branch); `src/worktrail/orchestrator/verify.py` (`_spawn_group_worker`,
  `_recheck_merged_before_quarantine`'s caller guard in `verify_one`, `run_all`'s result dict
  assembly).
- **Tests**: `tests/orchestrator/test_dispatch_extras.py` — the `ROLE_RESOLVE` prompt names the
  base-deletion rule. `tests/orchestrator/test_verify.py` — a resolve worker whose pushed commit
  trips the forbidden-path check and is then externally merged lands in
  `forbidden_path_violations`, not `merged` or `quarantined`; a group with no forbidden-path
  finding is unaffected and still uses the existing recheck.
- **Non-goals**: changing what counts as a forbidden path, or the two-tier
  `forbidden_prefixes_for()` scoping logic itself (both already correct and out of scope here);
  reverting or undoing a resolve worker's already-merged bad commit (out of band once merged —
  this change is about correct bookkeeping and prompt-level prevention, not automated remediation
  of a landed merge); changing `ROLE_ASSEMBLY_RESOLVE` or `ROLE_CI_FIX` prompts beyond what
  `ROLE_RESOLVE` needs (the base-deletion scenario is specific to merging an advanced base into an
  existing group branch, which is `ROLE_RESOLVE`'s job, not the pre-PR assembly-time or CI-fix
  paths).
