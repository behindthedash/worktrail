# Design

## Context

See proposal.md — Why. The relevant machinery (all in
`src/worktrail/orchestrator/live.py` unless noted):

- `dependency_start_ref` (live.py:1180) picks the ref a task worktree branches from:
  first existing dependency branch, siblings merged in, else bare `HEAD`. It has no
  notion of "dependency merged into base and branch deleted" — that case falls into the
  bare-`HEAD` fallback, and `HEAD` is the run-start local base (`_refresh_base_branch`
  runs once at `_full_real_inner` entry, live.py:4126, never after mid-run merges).
- `verify.cleanup_group` (verify.py:1131) deletes every delivered task's worktree AND
  branch once its group PR merges.
- `_dispatch_pending_tail` (live.py:3082) dispatches e2e/cleanup tail tasks only after
  every group is integrated/verified/merged — so for tail tasks the
  deleted-branch + stale-base state is deterministic, not a race.
- `_require_dependency_files` (live.py:2216) fails loud at dispatch when a dependency's
  declared file is missing. Its two downgrade paths cannot engage post-squash for a
  dependency this run drove: the journaled `head_sha` exists but is not an ancestor of
  the stale worktree HEAD (squash rewrote history), and the DONE-status downgrade
  requires `head_sha` to be absent.
- `integrate.py` (integrate.py:768–792, 843–848) already solves the same boundary for
  group branches: fall back to the pre-squash merge-base, then merge the remote base
  with `-X ours` (byte-identical content, resolve in our favor).

## Goals / Non-Goals

**Goals:**
- A stacked worktree created after a dependency's squash-merge + branch cleanup
  contains that dependency's content, for both schedulers and for fresh and resumed
  runs, without weakening `_require_dependency_files`' fail-loud contract.
- Reproduce the failure in the lifecycle harness (real integrate/verify/gh-shim path)
  before fixing it.

**Non-Goals:**
- No change to `_require_dependency_files` validation semantics (it stays the
  structural backstop; the fix is upstream in the carry).
- No handling of cross-spec `external_deps` at this boundary (not implicated in the
  incident; unchanged behavior).
- No re-refresh of the local base ref mid-run (`_refresh_base_branch` call sites
  unchanged; the carry merges into the task worktree only, so it works even when the
  base checkout is dirty and a ref move would be refused).

## Decisions

1. **Carry inside `add_stacked_worktree`, after sibling stacking** — a private helper
   runs when `remote`/`base` are supplied: for each dependency in `coordinator.DONE`
   whose task branch no longer exists, check its declared `files:` against the worktree
   (`_dependency_file_declared_path_exists`); if anything is missing, `git fetch
   <remote> <base>` (best-effort), resolve the freshest base ref
   (`<remote>/<base>` → local `<base>`), skip if it is already an ancestor of the
   worktree HEAD, else `git merge --no-edit -X ours <ref>`.
   - Why here and not `dependency_start_ref`: the decision needs the created worktree
     (file checks, merge target) and must also cover the some-branches-exist case,
     where the start ref alone cannot carry the merged content.
   - Why gate on a missing declared file: zero behavior change for every run where the
     content is already present; the merge only happens in the exact incident state.
   - Why `-X ours`: same rationale as integrate.py's squash reconciliation — the
     stacked side's content is byte-identical to the squashed base's version of the
     dependency content; favor the worktree for apparent conflicts.
   - On merge failure: abort the merge, print a WARN, and fall through —
     `_require_dependency_files` keeps the fail-loud terminal behavior with its
     forensic message (Scenario: Base ref unavailable).
2. **Thread `remote`/`base` as optional kwargs** defaulting to `None` (carry disabled
   when absent). Callers updated: `_pipeline_scheduler._ensure_wt` (has both in
   scope), sequential `ensure_wt` in `live_run_real` (gains optional `remote`/`base`
   params), `_full_real_inner`'s fan-out call and `_dispatch_pending_tail` (gains
   pass-through params; both call sites updated). The cassette/demo path
   (`live_run`) stays unchanged. The sequential path already routes kwargs through
   `_add_stacked_worktree_kwargs`, so narrower monkeypatched test doubles keep
   working.
3. **Harness fidelity: fake `gh pr merge` performs a real squash merge**
   (`git merge --squash` + commit in the scratch clone) instead of `--no-ff`, matching
   the merge methods the fake's own `repo view` advertises (squash-only) and the
   production repos that hit the incident. This is what makes the journaled
   `head_sha`-ancestry gap reproducible in the harness.
4. **New harness scenario** mirrors the incident topology (root impl task, two impl
   tasks depending on it, one `kind: e2e` tail task depending on both): full
   `_full_real_inner` run; groups merge and clean up; tail dispatch previously raised
   `WorktreeMissingDependencyFileError`, now completes with the tail file landing on
   remote main.

## Risks / Trade-offs

- [Merging the full base ref pulls unrelated base advances into the task branch] →
  acceptable and precedented: integrate.py's fallback merges the remote base into
  group branches the same way; those commits are already on base, so PR diffs are
  unaffected.
- [`-X ours` could mask a genuine textual conflict against base] → bounded: the merge
  only runs in the merged-dependency-content-missing state, the same trade
  integrate.py already accepted; `_require_dependency_files` still validates the
  declared files afterward.
- [Fake-gh squash change alters existing harness expectations] → intended: squash is
  what the fake advertises; existing scenarios assert content/journal outcomes, which
  squash preserves, and the dependent-group squash fallback in integrate.py gets real
  coverage.

## Migration Plan

Single PR; no data or CLI migration. Rollback = revert.
