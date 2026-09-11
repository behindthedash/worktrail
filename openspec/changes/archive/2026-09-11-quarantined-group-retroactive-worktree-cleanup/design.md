## Context

Worktrees for an orchestrator run live at `<repo>-worktrees/<spec_id>-<task_id>`
(task) and `<repo>-worktrees/<spec_id>-verify-<group>` (verify). The run journal
`<repo>-worktrees/run-<spec_id>.json` records per group only
`{pr_url, head_branch, state, quarantine_reason}`; group -> task membership is
recomputed from the cached RunPlan via `coordinator.plan_groups()` (see
`quarantine_selfcheck._group_files`, which the `quarantine-reconciliation` design
chose over persisting membership onto the journal).

Today `sweep_stale_worktrees.classify_worktree()` is purely git-derived. For a
quarantined group's task worktree the sequence is: clean -> no remote-tracking ref
(`has_unpushed_commits` returns `None`) -> `git cherry` shows `+` lines (the group PR
was squash-merged, so patch-ids differ) -> remote branch gone -> PR lookup by the
*task* branch name finds nothing -> `UNPUSHED`, keep. The worktree is permanently
unreclaimable even though its group's PR is merged.

## Goals / Non-Goals

**Goals**
- Reclaim, through the existing attended flow, the worktrees of a quarantined group
  whose work is confirmed landed.
- Do it with evidence that already exists (journal + RunPlan cache + PR state), no new
  persisted state, no journal schema change.
- Keep every existing keep rule (DIRTY, unpushed commits, liveness guard) in force.

**Non-Goals**
- Unattended deletion. The sweep's doctrine (`worktree-cleanup.md`: classify, show,
  confirm) is unchanged; the sweep still never removes anything.
- Changing `cleanup_group()`'s delivered-only gate, or re-entering the orchestrator
  after the fact. A finished run is not resumed just to run its own teardown.
- Flipping the journal group state. `quarantine-reconciliation` already hides
  reconciled findings from the dashboard; this change does not write journals.
- Group worktrees under `<repo>-integrate/` (owned by
  `integrate-stale-group-worktree-reclaim`).

## Decisions

### D1. Classification lives in the sweep, action stays in the attended flow

The brief offered "sync-before-teardown or the sweep". Sync-before-teardown runs
inside the session that just ran the orchestrator, which is exactly the session that
decided to keep the worktrees for review -- the merge that makes them reclaimable
happens later, in someone else's session or by hand. The sweep is the only thing
that revisits worktrees after the fact, so the retroactive rule belongs there. It
gains a bucket, not a delete: `worktree-cleanup.md`'s `cleanup-worktrees` action is
the one consumer with a human to confirm.

### D2. Attribution: RunPlan recomputation for tasks, name match for verify worktrees

`classify_worktree()` gets an optional journal-derived index built once per repo in
`sweep_repo()`: for every `run-<spec_id>.json` with a `QUARANTINED` group, map
`<spec_id>-<task_id>` (task ids lowercased, per `worktree.py`) and
`<spec_id>-verify-<group>` to `(spec_id, group, pr_url)`. Task ids come from a new
public `quarantine_selfcheck.group_task_ids(repo, spec_id, group)`, which
`_group_files` is refactored to call; missing RunPlan cache -> no attribution ->
existing behaviour. Only `QUARANTINED` groups are indexed: a `MERGED` group's
worktrees were already removed by `cleanup_group()`, and an `OPEN` group is still
someone's live work.

### D3. Merge evidence is either of two existing signals, checked only after the keep rules

For an attributed worktree that is clean and has no unpushed commits (the existing
DIRTY / UNPUSHED-commits checks run first and short-circuit unchanged):

1. `pr_url` is non-empty and `gh pr view <pr_url> --json state` reports `MERGED`
   (best-effort, same degrade-silently posture as `pr_state_for_branch`); or
2. `quarantine_selfcheck.reconcile_finding()` returns a record for that group
   (`base-branch-files` or `merged-pr-files`).

Either -> `{"state": "QUARANTINE-MERGED", "reclaimable": True, "reason": "<which
signal, and the PR url or method>"}`. Neither -> fall through to today's git-only
classification, which will keep it. Signal 2 is accepted because
`quarantine-reconciliation` already treats it as "the work landed" for the dashboard;
the sweep should not be stricter than the detector it reads. The check is made once
per group per sweep and cached, so a group with N task worktrees costs one `gh` call.

### D4. Attended flow reads the sweep's JSON instead of a fourth hand-rolled bucket

`worktree-cleanup.md` step 2 currently spells out the git commands per worktree. It
gains one instruction: run `worktrail-sweep-stale-worktrees --repo "$REPO" --json`
and treat `QUARANTINE-MERGED` entries as prunable alongside MERGED / GONE, quoting
their `reason` in the confirmation table. Step 3 (confirm, liveness guard, remove) is
unchanged, so the `worktree-deletion-liveness-guard` requirement "Guard applies
uniformly" still holds without being re-specified here. The scoped `<spec_id>-*`
invocation used by `close-stale` picks this up for free.

## Risks / Trade-offs

- **`gh` unavailable or offline** -> signal 1 silently absent; signal 2's
  `base-branch-files` still works offline. Worst case is today's behaviour (keep).
- **Verify worktree with a detached HEAD** -> `branch_of()` returns `None` and the
  existing `UNKNOWN` path keeps it. Acceptable; verify worktrees are normally on the
  group branch.
- **`gh pr view` on a URL from another repo** (fork relayed PR) -> non-zero exit ->
  treated as no evidence.
