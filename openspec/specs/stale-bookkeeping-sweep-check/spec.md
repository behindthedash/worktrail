# stale-bookkeeping-sweep-check Specification

## Purpose

Schedules the already-hardened `dashboard.py` stale-bookkeeping detection (which today only
runs during an interactively-triggered `/go` dashboard scan) as a third, independent per-repo
check inside `worktrail-spec-sync-sweep`'s existing weekly sweep, so a repo whose pending tasks
already shipped out-of-band gets a Drift Brief filed without anyone needing to remember to run
`/go` against it.

## Requirements

### Requirement: Stale-bookkeeping check runs as a third independent per-repo check

For each repo the sweep discovers (the same discovery already used by the sweep's existing
spec-sync-drift and checkbox-drift checks), `run_sweep()` SHALL additionally run a
stale-bookkeeping check that reuses `dashboard.py`'s existing stale-pending-task detection
(the same detection an interactive `/go` scan applies) against that repo's `docs/specs/` and
`openspec/changes/` trees, without re-implementing or duplicating that detection's own
git-freshness logic.

#### Scenario: Repo has one or more stale-pending tasks
- **WHEN** the stale-bookkeeping check finds at least one spec or OpenSpec change in a repo
  whose pending tasks are all classified stale by the reused detection
- **THEN** the check records that repo, along with the affected spec/change id(s) and stale
  task id(s), so it can be surfaced in a Drift Brief

#### Scenario: Repo has no stale-pending tasks
- **WHEN** the stale-bookkeeping check finds no repo spec/change with every pending task
  classified stale
- **THEN** the check reports no findings for that repo, and no brief is filed on its behalf

#### Scenario: The check errors for one repo
- **WHEN** the stale-bookkeeping check raises while evaluating a given repo (e.g. an unreadable
  spec directory)
- **THEN** the error is captured against that repo instead of propagating, this repo's other
  two independent checks (spec-sync-drift, checkbox-drift) still run for it, and every other
  discovered repo's stale-bookkeeping check still runs

### Requirement: Stale-bookkeeping check is independent of the sweep's other two checks

A repo's outcome on the stale-bookkeeping check SHALL NOT affect, and SHALL NOT be affected by,
that same repo's outcome on the spec-sync-drift check or the checkbox-drift check.

#### Scenario: A repo drifts on stale-bookkeeping only
- **WHEN** a repo has stale-pending tasks but no spec-sync drift and no checkbox-completion
  drift
- **THEN** exactly one stale-bookkeeping Drift Brief is filed for that repo, and no spec-sync
  or checkbox-drift brief is filed for it

#### Scenario: A repo drifts on all three checks
- **WHEN** a repo has stale-pending tasks, spec-sync drift, and checkbox-completion drift,
  simultaneously
- **THEN** each check files its own brief for that repo (up to three), each governed by its own
  dedup lookup, independent of whether the other checks found anything or errored

#### Scenario: The stale-bookkeeping check errors while the other two succeed
- **WHEN** the stale-bookkeeping check raises for a repo
- **THEN** that repo's spec-sync-drift and checkbox-drift checks still run and, if they find
  drift, still file their own briefs as usual

### Requirement: Exactly one dedup'd Drift Brief per repo

When the stale-bookkeeping check finds one or more stale-pending tasks in a repo (across one or
more specs/changes), the sweep SHALL file exactly one Drift Brief for that repo — never one per
affected spec, change, or task — unless an unresolved stale-bookkeeping Drift Brief already
exists for that repo, in which case no new brief is filed.

#### Scenario: Multiple stale-pending tasks across multiple specs in one repo
- **WHEN** a repo has stale-pending tasks in more than one spec or OpenSpec change
- **THEN** exactly one Drift Brief is filed for that repo, and its body lists every affected
  spec/change id and stale task id found

#### Scenario: An unresolved stale-bookkeeping brief already exists for the repo
- **WHEN** a repo already has a Drift Brief in the queue whose `repo` frontmatter matches that
  repo and whose `drift-source` frontmatter is `stale-bookkeeping-sweep`, and it is not marked
  resolved (`status: done` in `picked/`)
- **THEN** no new stale-bookkeeping brief is filed for that repo on this run, and the repo is
  recorded as already-outstanding rather than filed

#### Scenario: A prior stale-bookkeeping brief for the repo is already resolved
- **WHEN** a repo's only existing stale-bookkeeping brief is in `picked/` with `status: done`
- **AND** the repo has stale-pending tasks on this run
- **THEN** a new stale-bookkeeping Drift Brief is filed for that repo

### Requirement: The sweep's run record and CLI summary report the stale-bookkeeping check

`run_sweep()`'s returned record SHALL include `stale_bookkeeping_drifted`,
`stale_bookkeeping_filed`, `stale_bookkeeping_skipped_existing`, and `stale_bookkeeping_failed`
list fields, populated the same way the existing `drifted`/`filed`/`skipped_existing`/`failed`
and `checkbox_*` fields are populated for their respective checks. `main()`'s human-readable
summary output SHALL report the stale-bookkeeping counts alongside the existing spec-sync-drift
and checkbox-drift counts.

#### Scenario: A run with stale-bookkeeping findings, in JSON mode
- **WHEN** `spec_sync_sweep.py` is invoked with `--json` and at least one repo has
  stale-pending tasks
- **THEN** the printed JSON record includes non-empty `stale_bookkeeping_drifted` and
  `stale_bookkeeping_filed` (or `stale_bookkeeping_skipped_existing`) entries for that repo

#### Scenario: A run with no stale-bookkeeping findings
- **WHEN** no discovered repo has any stale-pending tasks
- **THEN** `stale_bookkeeping_drifted`, `stale_bookkeeping_filed`,
  `stale_bookkeeping_skipped_existing`, and `stale_bookkeeping_failed` are all empty lists, and
  the human-readable summary reports zero counts for the stale-bookkeeping check

#### Scenario: A run skipped due to single-flight overlap
- **WHEN** `run_sweep()` returns early because a previous run still holds the lock
  (`skipped_overlap: True`)
- **THEN** the stale-bookkeeping fields are present and empty, matching the existing
  `skipped_overlap` record shape for the other two checks

### Requirement: The sweep never mutates task status or opens a PR

The stale-bookkeeping check SHALL be read-only against every checked repo and SHALL perform no
git operation (commit, branch, `gh pr create`) and no task-status write-back against it. Filing
a Drift Brief is the only side effect; flipping a stale task's status to completed remains the
job of the existing interactive `close-stale` dispatch action, unchanged by this capability.

#### Scenario: Stale-pending tasks are found
- **WHEN** the stale-bookkeeping check identifies stale-pending tasks in a repo
- **THEN** the only side effect is writing a new Drift Brief file into the work queue; the
  checked repo's working tree, git history, and task files are left unmodified
