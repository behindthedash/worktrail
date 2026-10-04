## MODIFIED Requirements

### Requirement: Refusal leaves the remote untouched

Whenever the pipeline refuses — a dirty tree it was not asked to commit, a missing or stale
compile marker after the compile attempt, or a preflight denial — it SHALL make no push and
create no pull request, SHALL return a refused outcome naming the failed step together with a
detail that identifies the cause and quotes the step's own output where the step produced any,
and SHALL leave any run record it started in a non-terminal state that names the refusal.

Every refusal path SHALL populate that detail. A step name alone does not satisfy this: a
refusal whose cause cannot be told apart from the step name alone — an uncommittable dirty
tree, or a preflight denial — SHALL report the distinguishing cause and, for a denial, the
gate's own output, in the detail.

#### Scenario: Refusal after a local commit

- **WHEN** the pipeline has committed the caller's changes and the compile marker step then
  refuses
- **THEN** the head branch has no remote counterpart, no pull request exists, and the
  refused result names the compile step

#### Scenario: Preflight denial quotes the gate's output

- **WHEN** the pre-PR gate exits non-zero for the committed head
- **THEN** nothing is pushed, no pull request is created, and the refused result names the
  preflight step and carries the gate's own failure output as its detail, whether the gate
  wrote that output to stdout or to stderr

#### Scenario: Dirty-tree refusal reports the cause

- **WHEN** the pipeline refuses a dirty tree it was not asked to commit, whether because no
  commit message was supplied or because a git operation along the way failed
- **THEN** the refused result names the dirty-tree step and its detail identifies which of
  those causes fired, quoting the failing git operation's own stderr when there is one

#### Scenario: Every refusal carries a detail

- **WHEN** the pipeline returns a refused outcome for any locally-checkable failure
- **THEN** the refused result's detail is populated, never left empty
