## Purpose

Lets a repo opt in, per its own `go-policy.yaml`, to running named worktrail
add-ons after a task's own commit, staging and committing any file output
those add-ons produce into the same PR the task produced — without any
behavior change for repos that configure nothing.

## ADDED Requirements

### Requirement: Add-ons are opt-in per repo
The system SHALL run no add-on step, empty or otherwise, for a task unless
the repo's `docs/specs/go-policy.yaml` explicitly configures an `add_ons:`
block naming at least one enabled add-on.

#### Scenario: Repo with no add_ons config
- **WHEN** a task completes in a repo whose `go-policy.yaml` has no
  `add_ons:` key (the default for every repo unless configured)
- **THEN** no add-on install, configure, or run step executes, and no
  additional commit is created beyond the task's own work

#### Scenario: Repo with add_ons config
- **WHEN** a task completes in a repo whose `go-policy.yaml` declares
  `add_ons: { <name>: { enabled: true, ... } }`
- **THEN** the named add-on's run step executes as part of that task's
  pre-PR flow

### Requirement: Add-ons are pluggable behind a common interface
The system SHALL expose add-on behavior through a single interface (install,
configure, run) that a new add-on can implement without any change to the
orchestrator's group-PR path or the router's one-off preflight path.

#### Scenario: A second, unrelated add-on is added later
- **WHEN** a new add-on is implemented against the add-on interface and
  registered under a new name
- **THEN** it becomes usable via `add_ons: { <new-name>: {...} }` in any
  repo's policy without modifying `integrate.py` or `preflight.py`

### Requirement: Unknown add-on names fail closed
The system SHALL raise a clear, actionable error at policy-load or preflight
time if `go-policy.yaml` names an add-on that does not resolve to a known
implementation, rather than silently skipping it.

#### Scenario: Misconfigured add-on name
- **WHEN** `go-policy.yaml` declares `add_ons: { typo-name: {enabled: true} }`
  and no add-on named `typo-name` is registered
- **THEN** policy loading or the preflight run reports the unresolved
  add-on name and does not silently proceed as if nothing were configured

### Requirement: Add-on output is staged and committed before push
The system SHALL, after an enabled add-on's run step completes, stage any
files it changed and commit them (`git add` the affected paths, then commit
only if `git diff --cached --quiet` reports changes) before the branch is
pushed or a PR is opened or reused — mirroring the existing
add → diff-check → commit pattern already used for task-status bookkeeping.

#### Scenario: Add-on produces file changes
- **WHEN** an enabled add-on's run step modifies or creates files in the
  working tree
- **THEN** those files are staged and committed with a message identifying
  the add-on, before the branch is pushed

#### Scenario: Add-on produces no file changes
- **WHEN** an enabled add-on's run step completes without changing any
  tracked or untracked files it owns
- **THEN** no empty commit is created

### Requirement: Hook runs in both PR paths
The system SHALL invoke the add-on stage-and-commit step in both the
group-PR integration path and the one-off single-task PR path, after the
relevant commit(s) exist in the target worktree and before that path's
pass/fail smoke/drift gates and before push.

#### Scenario: Group-PR path
- **WHEN** a group's task branches have been merged into the group's
  integration worktree during group integration
- **THEN** enabled add-ons run and their output is committed before the
  drift gate, smoke gate, and push for that group

#### Scenario: One-off single-task path
- **WHEN** an agent has committed a single task's own work and invokes the
  preflight run step before opening its PR
- **THEN** enabled add-ons run and their output is committed before the
  preflight pass/fail gate and before the agent opens or updates its PR

### Requirement: Add-on failures do not block delivery by default
The system SHALL treat a failing add-on run as non-fatal by default (logged
and skipped, with no commit for that add-on), and SHALL support an explicit
per-add-on configuration flag that makes a failing run fail-closed (blocking
the PR path) for repos that require it.

#### Scenario: Default non-fatal failure
- **WHEN** an enabled add-on's run step errors or times out and its
  configuration does not request strict/required behavior
- **THEN** the failure is logged, no commit is made for that add-on, and the
  task's pre-PR flow continues to its normal gates and push

#### Scenario: Strict add-on configured
- **WHEN** an enabled add-on's configuration marks it as required/strict and
  its run step errors or times out
- **THEN** the pre-PR flow for that path stops before push, the same way an
  existing failing drift/smoke gate stops it
