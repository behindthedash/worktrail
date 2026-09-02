## MODIFIED Requirements

### Requirement: Stale-bookkeeping check runs as a third independent per-repo check

For each repo the sweep discovers (the same discovery already used by the sweep's existing
spec-sync-drift and checkbox-drift checks), `run_sweep()` SHALL additionally run a
stale-bookkeeping check that reuses `dashboard.py`'s existing stale-pending-task detection
(the same detection an interactive `/go` scan applies) against that repo's `docs/specs/` and
`openspec/changes/` trees, without re-implementing or duplicating that detection's own
git-freshness logic. The reused detection SHALL treat a devkit task as *reconciled* — neither
pending work nor stale bookkeeping — when it carries the frontmatter opt-out
`stale-sweep: exempt`, or when its `status:` is `implemented` and its body carries a
`> **UI REMOVED …**` or `> **SUPERSEDED …**` blockquote reconciliation marker.

#### Scenario: Repo has one or more stale-pending tasks
- **WHEN** the stale-bookkeeping check finds at least one spec or OpenSpec change in a repo
  whose pending tasks are all classified stale by the reused detection
- **THEN** the check records that repo, along with the affected spec/change id(s) and stale
  task id(s), so it can be surfaced in a Drift Brief

#### Scenario: Repo has no stale-pending tasks
- **WHEN** the stale-bookkeeping check finds no repo spec/change with every pending task
  classified stale
- **THEN** the check reports no findings for that repo, and no brief is filed on its behalf

#### Scenario: The only non-completed task is a reconciled implemented task
- **WHEN** a devkit spec's sole non-`completed` task is `status: implemented`, its `files:`
  are all git-tracked on base, and its body carries a `> **UI REMOVED …**` reconciliation
  marker (or the task carries `stale-sweep: exempt`)
- **THEN** the reused detection classifies no task in that spec as stale, the spec is not
  reported as stale-bookkeeping, it is not routed to the orchestrator, and no brief is filed

#### Scenario: A reconciliation marker on a task that is not implemented
- **WHEN** a `status: pending` task carries the same blockquote marker and all its `files:`
  are git-tracked on base
- **THEN** the task is still classified stale (the marker only reconciles `implemented` tasks)

#### Scenario: The check errors for one repo
- **WHEN** the stale-bookkeeping check raises while evaluating a given repo (e.g. an unreadable
  spec directory)
- **THEN** the error is captured against that repo instead of propagating, this repo's other
  two independent checks (spec-sync-drift, checkbox-drift) still run for it, and every other
  discovered repo's stale-bookkeeping check still runs
