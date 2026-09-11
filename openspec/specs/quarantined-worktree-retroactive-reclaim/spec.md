# quarantined-worktree-retroactive-reclaim Specification

## Purpose
Lets the worktrees an orchestrator run kept for a quarantined group be reclaimed
once that group's work is confirmed landed, through the existing report-only sweep
and attended cleanup flow, instead of staying on disk forever because
`cleanup_group()` only runs on the delivered-merge path.
## Requirements
### Requirement: Journal-aware attribution of orchestrator worktrees to quarantined groups

The stale-worktree sweep SHALL, once per swept repository, read every
`<repo>-worktrees/run-<spec_id>.json` journal and, for each group whose `state` is
`QUARANTINED`, attribute to that group the worktree directories named
`<spec_id>-<task_id>` for every task id the group contains (recomputed from the
cached RunPlan via `coordinator.plan_groups()`, exposed as a public
`quarantine_selfcheck.group_task_ids()` helper that the existing file-set
recomputation also uses) and the directory named `<spec_id>-verify-<group>`. Groups
in any other state SHALL NOT be attributed. A missing or unreadable RunPlan cache
SHALL leave that group's task worktrees unattributed.

#### Scenario: Task and verify worktrees of a quarantined group are attributed
- **WHEN** the journal records group `feature-1` of spec `S` as `QUARANTINED`, the
  cached RunPlan places tasks `1.1` and `1.2` in `feature-1`, and worktrees
  `S-1.1`, `S-1.2`, and `S-verify-feature-1` exist
- **THEN** all three worktrees are attributed to `(S, feature-1)` with that group's
  `pr_url`

#### Scenario: Merged or open groups are not attributed
- **WHEN** the journal records a group as `MERGED` or `OPEN`
- **THEN** none of its worktrees are attributed and they are classified by the
  existing git-only rules

#### Scenario: RunPlan cache is missing
- **WHEN** a `QUARANTINED` group exists but no `<repo>-worktrees/runplans/<spec_id>-*.json`
  file can be read
- **THEN** its task worktrees are not attributed and are classified by the existing
  git-only rules, while its `<spec_id>-verify-<group>` worktree is still attributed
  by name

### Requirement: Confirmed-merged quarantined group worktrees classify as reclaimable

For an attributed worktree that is clean and has no unpushed commits, the sweep SHALL
classify it as state `QUARANTINE-MERGED` with `reclaimable: true` when either the
group's `pr_url` resolves via `gh pr view` to a PR whose state is `MERGED`, or
`quarantine_selfcheck.reconcile_finding()` returns a reconciliation record for that
group. The `reason` SHALL name which signal matched and its evidence (the PR URL or
the reconciliation method). The merge-evidence check SHALL run at most once per group
per sweep. When neither signal matches, or `gh` is unavailable, unauthenticated, or
fails, the worktree SHALL fall through to the existing git-only classification.

#### Scenario: Group PR confirmed merged
- **WHEN** an attributed, clean worktree's group has a `pr_url` whose live state is
  `MERGED`
- **THEN** the sweep reports it as `QUARANTINE-MERGED`, reclaimable, with a reason
  naming the PR URL, even though `git cherry` still shows commits unique to its
  branch and no remote-tracking ref exists

#### Scenario: Group reconciled without a merged PR record
- **WHEN** an attributed, clean worktree's group has an empty `pr_url` but
  `reconcile_finding()` resolves it by `base-branch-files`
- **THEN** the sweep reports it as `QUARANTINE-MERGED`, reclaimable, with a reason
  naming the reconciliation method

#### Scenario: No merge evidence
- **WHEN** an attributed, clean worktree's group has a `pr_url` whose live state is
  `OPEN` and `reconcile_finding()` returns nothing
- **THEN** the worktree is classified exactly as it is today (not reclaimable)

#### Scenario: One evidence lookup per group
- **WHEN** three worktrees are attributed to the same quarantined group
- **THEN** the PR state and reconciliation lookups for that group run once during
  the sweep, not once per worktree

### Requirement: Safety guards are never bypassed

Journal attribution SHALL NOT override the existing keep rules: a dirty worktree or
one with unpushed local commits SHALL keep its `DIRTY` / `UNPUSHED` classification
regardless of merge evidence, and the sweep SHALL remain report-only. Removal of a
`QUARANTINE-MERGED` worktree happens only in the attended cleanup flow, after the
`worktree-deletion-liveness-guard` check, exactly as for `MERGED` / `GONE`.

#### Scenario: Dirty worktree of a merged quarantined group
- **WHEN** an attributed worktree has uncommitted changes and its group's PR is
  `MERGED`
- **THEN** the sweep reports it as `DIRTY`, not reclaimable

#### Scenario: Sweep output only
- **WHEN** the sweep classifies a worktree as `QUARANTINE-MERGED`
- **THEN** the worktree, its branch, and the run journal are unmodified after the
  sweep returns

### Requirement: Attended cleanup consumes the journal-aware classification

`worktrail-go/references/worktree-cleanup.md`'s classification step SHALL instruct
the agent to obtain each worktree's bucket from
`worktrail-sweep-stale-worktrees --repo "$REPO" --json` and to treat
`QUARANTINE-MERGED` entries as prunable alongside `MERGED` / `GONE`, showing the
entry's `reason` in the confirmation table. The confirm, liveness-guard, and remove
steps SHALL be unchanged, and the scoped `<spec_id>-*` invocation SHALL apply the
same bucket.

#### Scenario: Cleanup action reclaims a quarantined group's worktrees after its PR landed
- **WHEN** an agent runs the `cleanup-worktrees` action and the sweep reports a
  `QUARANTINE-MERGED` worktree
- **THEN** the procedure lists it as prunable with its reason, asks for the single
  confirmation, runs the liveness guard, and only then removes it

#### Scenario: Cleanup notes remain consistent
- **WHEN** the reference's Notes section describes a quarantined group's worktree
- **THEN** it states that such a worktree is kept until the sweep reports it
  `QUARANTINE-MERGED`, rather than that it is never auto-deleted

