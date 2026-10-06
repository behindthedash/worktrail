# out-of-band-delivery-terminal-state Specification

## Purpose

Recognizes a group integration whose content the target branch already contains -- delivered
out-of-band by another PR (a tail-reconciliation PR, a checkbox-sync PR, a squash-merged group PR)
-- as a terminal, non-quarantining delivered outcome at every seam of the shared group integration
path, so such a group is never reported as an unreconciled failure, never cascades
`dependency_quarantined` onto dependents whose work did ship, and never retains its branch and
worktrees for human review.

## ADDED Requirements

### Requirement: A group whose content the target already contains is terminal, not quarantined

At every seam of the shared `integrate_one` path at which an integration can be concluded to need a
pull request, the system SHALL recognize a group whose content the target already contains
("delivered out-of-band") and SHALL record it in the terminal delivered state instead of
quarantining it. The terminal delivered state SHALL be the run journal's existing `MERGED` group
state with an empty `pr_url`, the group branch as `head_branch`, and no `quarantine_reason`; no new
journal state vocabulary SHALL be introduced. A group recorded this way SHALL NOT enter the
quarantined set, so no dependent group SHALL be quarantined by a `dependency_quarantined` cascade
on its account, and the console SHALL name the group, state that its content was delivered
out-of-band, and print the evidence the verdict rests on. The recognition SHALL be content-based: a
branch contributes nothing to a ref when it has no commits beyond that ref, or when its changed
content is already present in it; the *absence* of an ancestry link SHALL NOT be read as "not
contained", because a squash merge leaves the branch no ancestry link to the base even though its
content landed.

#### Scenario: Content delivered out-of-band is not an unreconciled failure
- **WHEN** a resumed run reuses a group branch whose content the base branch already contains,
  because a tail-reconciliation (or other out-of-band) PR landed it
- **THEN** the group is recorded `MERGED` with an empty `pr_url` and no `quarantine_reason`, the
  run logs the delivery as out-of-band with its evidence, and every dependent group proceeds
  against the base branch instead of being quarantined by a cascade

#### Scenario: A delegate that never committed anything is not delivered
- **WHEN** the fresh-build path merges deliverable task branches that carry no commits beyond the
  target
- **THEN** the existing empty-diff classification applies unchanged and the group is not recorded
  as delivered out-of-band

### Requirement: A group branch that contributes nothing to its PR base terminates before the PR is opened

After the group branch is resolved -- freshly built or reused from the remote -- and immediately
before the shared PR open/update step, the system SHALL evaluate the branch against the ref its
pull request would target, preferring the remote-tracking base ref over a local branch name, and
SHALL record the terminal delivered state without calling the shared open/update step when the
branch has no commits beyond that ref. GitHub refuses `gh pr create` for exactly this input with
`No commits between <base> and <branch>`, so this precondition SHALL be recognized before the call
rather than from the refusal; a `gh pr create` refusal SHALL NOT be the first evidence that a
branch had nothing to ship, and this check SHALL NOT change the existing quarantine recorded for
any other `gh pr create` failure. A ref that does not resolve SHALL never be read as containment.

#### Scenario: A resumed run's reused branch terminates without a PR attempt
- **WHEN** the remote group branch already exists, its fetched copy has no commits beyond the ref
  the PR would target, and the run resumes the group
- **THEN** the shared PR open/update step is never called for that group, and the group is recorded
  `MERGED` with an empty `pr_url`, no `quarantine_reason`, and the zero-commits evidence in the log

#### Scenario: An unresolvable PR base is not containment
- **WHEN** the ref the PR would target does not resolve in the integrating repository
- **THEN** nothing is concluded from it, the ordinary path runs, and a refusal from the shared
  open/update step is quarantined exactly as it is today

#### Scenario: A branch carrying unshipped commits still opens its PR
- **WHEN** the group branch has commits beyond the ref its PR would target
- **THEN** the PR is opened or reused exactly as before this change, with no delivered-out-of-band
  verdict

### Requirement: A merge conflict is never by itself evidence of delivery

Before recording a merge-conflict quarantine, the system SHALL consult the delivered-out-of-band
recognition using conclusive containment only: the branch is an ancestor of the target, or a clean
three-way merge of the branch into the target reproduces the target's own tree. The shared content
vocabulary's conflicted-merge shortcut -- a conflicted three-way merge counting as contained, which
the dependency-stacking and stale-worktree call sites rely on -- SHALL NOT decide a terminal
verdict: a branch whose changes the target does not have and whose merge conflicts with it SHALL
still be quarantined with the merge-conflict reason and retained, exactly as today.

#### Scenario: A conflict whose branch carries changes the target lacks still quarantines
- **WHEN** merging a deliverable task branch onto the integration target conflicts and the branch's
  changes are not conclusively contained in the target
- **THEN** the group is recorded `QUARANTINED` with the merge-conflict reason and its branch is
  retained for human review, unchanged from today

#### Scenario: A conflicted merge is not read as containment
- **WHEN** the three-way merge of a deliverable task branch with the target conflicts
- **THEN** no containment is inferred from the conflict itself; only ancestry or a clean merge that
  reproduces the target's tree can conclude the delivered verdict

### Requirement: A stacked deliverable with nothing of its own is reconciled, not conflicted

A deliverable task branch whose own commits -- the commits beyond the dependency start ref it was
stacked on, resolved unpruned so the stacked prefix is visible -- contribute nothing the target
lacks, and whose start ref's content the target already contains, SHALL be recorded in the terminal
delivered state without a merge being attempted and without a merge-conflict quarantine. When the
stacked start ref cannot be resolved, the system SHALL conclude nothing and the ordinary path SHALL
run, and an attempt whose branch does carry content the target lacks SHALL take the ordinary path.

#### Scenario: Verification-only task stacked on an already-landed dependency
- **WHEN** a reconciliation attempt runs for a task whose branch carries no commits of its own
  beyond its stacked dependency start ref and the target already contains that start ref's content
- **THEN** the attempt records the terminal delivered state without merging, and the task's
  reconciliation outcome is reported as merged rather than as a merge-conflict quarantine

#### Scenario: Stacked start ref cannot be resolved
- **WHEN** the dependency start ref a task was stacked on no longer resolves
- **THEN** nothing is concluded from it, the merge is attempted as before, and a conflict is
  quarantined with the merge-conflict reason

#### Scenario: Own commits carrying content the target lacks are shipped normally
- **WHEN** a task branch stacked on a dependency carries commits of its own whose content the
  target does not have
- **THEN** the ordinary path runs and the group's PR ships them

### Requirement: A delivered-out-of-band group is torn down, not retained for review

A group recorded in the terminal delivered state SHALL NOT be retained for human review. The group
branch SHALL be removed best-effort -- a local branch delete plus a remote delete when the branch
exists on the remote -- under the same git lock the integration holds, and a deletion failure SHALL
be logged without turning the terminal verdict back into a quarantine. The group's task worktrees
and task branches SHALL be left to the ordinary post-merge cleanup and stale-worktree sweep paths,
which classify a group that is not quarantined by their existing git-only rules, and no run summary
or finding SHALL report the group as quarantined or its content as undelivered.

#### Scenario: The retained branch of a delivered group is removed
- **WHEN** the terminal delivered verdict is recorded for a group whose branch exists locally and on
  the remote
- **THEN** both the local and remote copies of the branch are deleted, the removals are logged, and
  the group record stays `MERGED`

#### Scenario: A cleanup failure does not resurrect the quarantine
- **WHEN** deleting the group branch fails (for example the remote rejects the delete)
- **THEN** the failure is logged, the group remains `MERGED` with an empty `pr_url`, and no
  quarantine record is written for it
