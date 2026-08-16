## Purpose

Prevents a git worktree/branch removal from silently destroying another still-live
session's uncommitted work, by checking the removal target's owning run record for
liveness before deleting it.

## ADDED Requirements

### Requirement: Worktree-to-run-record lookup
`run_record.py` SHALL provide a read-only lookup that, given a run-records directory,
a repository path, and a worktree path, returns the most recently started non-terminal
run record whose stored `worktree` field resolves to that same path, or reports no
match if none exists. A malformed run record file encountered during the scan SHALL be
skipped (with a warning) rather than aborting the lookup.

#### Scenario: Exactly one run record owns the worktree
- **WHEN** the lookup is run for a worktree path and exactly one run record under the
  repository's run-records directory has that path as its `worktree` field
- **THEN** the lookup returns that run record's path

#### Scenario: No run record owns the worktree
- **WHEN** the lookup is run for a worktree path and no run record under the
  repository's run-records directory has that path as its `worktree` field
- **THEN** the lookup reports no owning run record, distinguishable from a match

#### Scenario: Malformed record does not abort the scan
- **WHEN** the run-records directory contains one file that was rewritten outside
  `run_record.py`'s own renderer (unparseable) alongside other valid records
- **THEN** the malformed file is skipped and reported as a warning, and the lookup
  still returns a match from the remaining valid records if one exists

### Requirement: Run record tracks its own worktree path
Every procedure that creates a worktree for a run SHALL record that worktree's path on
the run's own run record immediately after creation, so a later lookup by worktree
path can find it.

#### Scenario: Worktree path recorded after creation
- **WHEN** a worktree is created for run record `RUN` at path `WT`
- **THEN** `RUN`'s `worktree` field is set to `WT` before any further work proceeds in
  that worktree

### Requirement: Deletion liveness guard
Before any `git worktree remove` / branch deletion pair runs against a worktree path,
the caller SHALL look up that worktree's owning run record and check its liveness
(heartbeat freshness and dispatch identity) using the caller's own dispatch identity.
If the owning run record is both fresh (recently active) and owned by a different
dispatch than the caller, the caller SHALL refuse to remove the worktree or its branch
and SHALL report the conflict instead of deleting anything. When no owning run record
is found, or the owning record is stale, or the owning record belongs to the caller's
own dispatch, the removal SHALL proceed unchanged from prior behavior.

#### Scenario: Live foreign owner blocks deletion
- **WHEN** a worktree removal is about to run and the worktree's owning run record is
  fresh and its dispatch identity differs from the caller's own dispatch identity
- **THEN** the worktree and its branch are not removed, and the conflict (owning
  run's id and last-heartbeat age) is reported instead

#### Scenario: Same-dispatch owner does not block deletion
- **WHEN** a worktree removal is about to run and the worktree's owning run record's
  dispatch identity matches the caller's own dispatch identity
- **THEN** the removal proceeds unchanged, regardless of the record's freshness

#### Scenario: Stale or absent owner does not block deletion
- **WHEN** a worktree removal is about to run and the worktree's owning run record is
  either not found or found but stale (no recent heartbeat)
- **THEN** the removal proceeds unchanged

### Requirement: Guard applies uniformly across all documented deletion paths
The deletion liveness guard SHALL be applied at every documented `git worktree
remove`/branch-deletion call site that targets a worktree created by one of these
procedures: the orchestrated `new`-pipeline teardown, the direct fix-branch worktree
teardown, and the dashboard-driven stale-worktree cleanup flow. The cleanup flow,
which has no run of its own in progress, SHALL derive its run-records directory from
the repository's resolved policy rather than from an in-progress run.

#### Scenario: Stale-worktree cleanup flow honors the guard
- **WHEN** the dashboard-driven cleanup flow has classified a worktree as safe to
  prune and is about to remove it
- **THEN** it first applies the deletion liveness guard for that worktree before
  removing it, using the repository's policy-resolved run-records directory
