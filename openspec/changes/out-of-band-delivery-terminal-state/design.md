## Context

`integrate_one` (`src/worktrail/orchestrator/integrate.py:1436`) is the single seam every group
integration goes through: the ordinary dependency-ordered groups the pipeline scheduler fans out
(`live.py:6242`), and the synthetic `tail-<task-id>` groups `reconcile_unreconciled_tail_evidence`
builds for unreconciled tail findings (`integrate.py:2233`). Within it, the fresh-build path already
recognizes one delivered-out-of-band shape: after merging the deliverable task branches into a
worktree at `target`, an empty diff against `target` is not a no-op delegate when every deliverable
task's branch carries commits whose content `target` already has
(`_deliverable_already_in_target`, `integrate.py:110`, guarded at `1687-1710`). That recognition
came from live losses: aspens run go-20260918-193109 lost feature-1/3/4 to `dependency_quarantined`
cascades after per-task tail PRs had already landed the content.

Every other seam lacks it. The remote-branch reuse path (`1644-1653`) fetches an existing branch and
drops to the PR reconcile with no delivery check; the PR open/update step quarantines any
`gh pr create` refusal as `integration_error` (`1936-1947`); the merge-conflict branch quarantines
`merge_conflict` before any content check (`1673-1677`). The content vocabulary the existing guard
uses is shared (`shared/git_merged.py`: `branch_content_in_base` at line 34, `has_commits_beyond`
at line 68), and it is already deliberate about the squash shape: commit ancestry and `git cherry`
both mis-answer it, so containment is decided by a three-way merge whose result equals the base's
own tree -- with a documented exception (line 42) that a *conflicted* merge-tree also counts as
contained, for retained shared-pr-landing-pipeline branches whose verbatim diff never appears in
base because the group PR squash-merged with review fixups on top.

See `proposal.md` for the live evidence (run go-20261005-084258) and the corrected framing. This
document records the decisions behind the rule's shape, the evidence standard for a *terminal*
verdict, and the rejected alternatives.

## Goals / Non-Goals

**Goals:**

- A group integration whose content the target already contains never quarantines, at any seam
  through which `integrate_one` can conclude a PR is needed, and never retains its branch or
  worktrees for human review.
- Terminal verdicts rest on evidence that cannot silently discard unshipped work: the standard is
  conclusive containment, and the shape a terminal verdict is recorded in is unchanged from what
  the journal already carries for "nothing to open a PR for".
- The reuse path -- the seam the cited run actually hit -- is covered even when the deliverable's
  task branches are no longer resolvable locally, which is the normal state of a resumed run whose
  task branches were cleaned up after their work landed.
- The tail reconciliation half needs no separate flow: its findings already reach the same seam,
  so the carve-outs land there once and the tail spec's contract is amended to match.

**Non-Goals:**

- Changing `detect_unreconciled_evidence`'s delivery-ledger invariant (`integrate.py:741`). It
  flags a DONE task whose branch HEAD is not an ancestor of `<remote>/<base>`, and a squash merge
  makes that true for content that *did* land -- but the invariant's job is detection for a human,
  and its false-positive rate for the landed shape is what the reconcile path's outcome vocabulary
  exists to absorb. Tightening the detector to content containment is a separate decision with a
  much wider blast radius (every DONE task's delivery proof).
- The no-op-delegate classification (`QUARANTINE_EMPTY_DIFF`, `integrate.py:1711-1728`,
  `orchestrator-foreign-repo-commit-detection`). A delegate that self-reported done without
  implementing anything must keep failing, and the rule's evidence preserves that where the
  question is decidable: the fresh-build guard's content test requires every deliverable task
  branch to carry commits beyond the target, and the stacked-own-contribution seam fires only for a
  branch whose own commits are empty *and* whose stacked start ref's content the target already
  contains -- the same shape the empty-diff guard already classifies as delivered today, consulted
  earlier so a conflict cannot pre-empt it. A delegate whose dependency has not landed takes the
  ordinary path unchanged.
- Running the drift/smoke gates on the reuse path. The reuse path skipping those gates is
  pre-existing behavior documented on the function's own docstring
  (`integrate.py:1484`: smoke "Only runs when the branch is freshly built"); a delivered-out-of-band
  branch has nothing to gate, so this change neither widens nor narrows that contract.
- `branch_content_in_base`'s conflicted-merge shortcut for its existing callers
  (`live.py:2117-2172`'s dependency stacking, `router/sweep_stale_worktrees.py:215`). For a
  *stacking* decision "treat it as superseded and fork from base" is cheap and recoverable; for a
  *terminal* verdict it is not, which is why the variant exists (below) instead of a change to the
  shared default.
- A new console command, policy key, or operator procedure: a delivered-out-of-band group needs no
  human step, which is the point.

## Decisions

### One rule, consulted at every seam; evidence is content-based

The recognition is extracted into one predicate beside the existing guard and consulted at four
seams: the post-merge empty-diff guard (existing), the remote-branch reuse path, the step
immediately before the shared PR open/update call, and the merge-conflict path. Each seam keeps its
own entry conditions and log line, but the evidence standard and the recorded outcome are the
rule's, not the seam's -- otherwise the next seam added to `integrate_one` repeats the same hole.

Evidence is content-based and requires the deliverable's own commits where that question is
decidable:

- **Branch contributes nothing to its PR base** (`git rev-list --count <pr_base>..<gb> == 0`, both
  refs resolving): conclusive about what a PR from that branch could ship. This is precisely the
  precondition of GitHub's `No commits between` refusal, so a terminal verdict here is not a guess
  about GitHub's behavior -- it is that refusal's own input.
- **Deliverable content contained** (the existing `_deliverable_already_in_target`: every
  deliverable task branch has commits beyond `target` whose content `target` already has): the
  fresh-build guard's existing standard, which also keeps "the delegate never committed anything"
  a distinct failure.
- **Never commit ancestry alone as the negative answer**: a squash merge leaves no ancestry link,
  which is the documented reason this vocabulary exists at all (`git_merged.py:1-11`).

Rejected: a per-seam ad-hoc check (the shape the code is in today, and how the hole appeared);
matching GitHub's refusal text after the fact (`"No commits between"` string matching -- it burns
the failing call, encodes a provider's error prose as a contract, and cannot distinguish the
refusal from any other `gh` failure); and flagging on the journal/PR state alone (the group has no
PR by construction in the failing shape).

### Terminal verdicts use conclusive containment only; the conflicted-merge shortcut is excluded

`branch_content_in_base` treats a conflicted three-way merge as "contained" (`git_merged.py:42-52`),
justified for the *stacking* decisions it was built for: a retained task branch that conflicts with
base is not a usable stacking point either way, so forking the dependent from base costs nothing.

A terminal verdict is a different question with a different failure mode. If a branch's changes are
genuinely absent from base and base has diverged on the same lines, the three-way merge conflicts
*for the opposite reason* -- and answering "contained" would record a delivered-out-of-band MERGED
and never ship the work, silently. That is strictly worse than the false quarantine this change is
fixing, because a quarantine is visible and recoverable while a false MERGED is neither. So the
terminal rule uses a conclusive-containment variant (`branch_content_in_base` with the
conflicted-merge shortcut disabled, defaulted off for the new call sites so every existing caller
keeps today's semantics), and a conflicted merge that is not conclusively contained still
quarantines with `QUARANTINE_MERGE_CONFLICT`, exactly as today.

Rejected: reusing the shortcut inside the terminal rule (silent loss above); and pre-emptively
resolving the conflict to decide (would mutate the integration worktree for a classification
question, and a resolve worker for every conflict is spend the quarantine-then-human loop does not
currently pay).

### The reuse-path check is branch-level, not task-branch-level

The cited run's group branch `full-1791215255/feature-1` is still on the remote and is an ancestor
of `main` with zero commits beyond it, while its task branches no longer exist locally (they were
deleted once their work landed -- the ordinary post-merge cleanup). A check built only on
`_deliverable_already_in_target` would resolve no task branch, return False, fall through to
`gh pr create`, and reproduce the exact false quarantine on the exact resumed run. The branch-level
form does not have that failure: a reused branch contained in its PR base cannot ship anything, so
the verdict is a statement about the branch, and it holds whether or not the task branches are still
on disk.

Two guards bound it. First, the comparison uses the ref the PR would actually target, preferring
the remote-tracking base ref (`<remote>/<base>`) over the local branch name: local `main` being
*behind* the remote makes containment-in-local a safe (stronger) statement, while a local ref
*ahead* of the remote would not be; and a ref that fails to resolve is "could not compute", never
"contained". Second, the no-op-delegate shape cannot reach this seam by the orchestrator's own
writes: the fresh-build path returns at the empty-diff guard *before* the push (`1687` vs `1772`),
and the status/add-on commits that follow it always make the branch differ from `target`. The only
way the orchestrator produces a group branch contained in base is the push-rejection rebase retry
(`1772-1797`), whose `git rebase <remote>/<base>` drops commits whose patches are already upstream
-- the delivered shape itself. A branch contained in base that arrived from anywhere else (an
operator push, another run) also cannot ship anything; the log line and journal record name the
evidence, so the classification is auditable either way.

### A stacked deliverable with nothing of its own records the delivered/merged outcome

The tail seam's shape is a task branch stacked by `add_stacked_worktree` (`live.py:2519`) on its
dependency's branch; the dependency's content lands out-of-band (the tail PR squash-merges), and the
stacked branch's merge onto base then conflicts over the dependency's hunks while the task itself
contributed nothing (the cited run's 2.1: "verification-only ... expect zero file changes"). The
truthful outcome is "nothing of its own to reconcile", and the rule says exactly that: a deliverable
whose branch carries **no commits beyond the dependency start ref it was stacked on** -- with that
start ref resolved *unpruned* (existence only; `live.dependency_start_ref`'s `base_ref` pruning must
not be applied here, because it substitutes the target once the dependency has landed and hides the
very prefix being measured) -- and whose start ref's content the target already contains, is
recorded as delivered/merged without attempting the merge.

The start-ref containment test uses the established `git_merged` vocabulary including the squash
shape, and here the conflicted-merge shortcut is *not* excluded -- deliberately, and for the reason
the shortcut exists: a dependency branch whose PR squash-merged with fixups is exactly the shape
whose verbatim diff is absent from base and whose merge conflicts. If the start ref cannot be
resolved at all, nothing is concluded and the ordinary path runs; the conservative failure
direction is the current quarantine.

Accepted boundary: a false positive here (the start ref's content is genuinely absent from base but
the shortcut reads it as contained) means this *leaf* attempt does not ship the dependency's
commits. The dependency's own finding remains its delivery evidence -- `_tail_superseded_by_map`
exists precisely to route the ancestor's commits through the leaf when they are only carried there,
and the run's own detector re-flags the leaf's HEAD while it is not an ancestor of the base. The
verdict is recorded with its evidence in the log and the journal record, so the case is auditable
rather than silent.

Rejected: leaving the tail seam to the detector (the brief's "should not reach a reconcile step at
all" -- true in spirit, but `detect_unreconciled_evidence` cannot tell a stacked no-op from real
unmerged work without the same content analysis, and it is the *reconcile* outcome vocabulary the
run-complete note renders); conclusive-only containment for the start ref (does not fire for the
observed squash shape, leaving the reported false quarantine in place); and treating the conflict
itself as containment (the silent-loss failure mode above).

### The terminal record is the existing MERGED state, and it is torn down like a merge

`journal` group records carry `{pr_url, head_branch, state, quarantine_reason?}` and `state` is one
of a small vocabulary every reader consumes (`TERMINAL_GROUP_STATES` at `integrate.py:44`; the
dashboard, `quarantine_selfcheck`, `learning/digest.py`, and resume's
`_group_branch_from_journal`). "Nothing to open a PR for" is already representable: the
implicit-merge path records `MERGED` with an empty `pr_url` and a synthetic head branch
(`integrate.py:1560-1565`), and the pipeline scheduler already special-cases a MERGED record written
during integrate by skipping VERIFY rather than chasing a PR that does not exist
(`live.py:6279-6289`). A new state (e.g. `DELIVERED`) would ripple through every consumer for a
distinction none of them needs -- the console line and the seam's log evidence carry the "why".

Teardown follows from the state: not being in the quarantined set is what removes the group from
the retention-and-human-review doctrine, and the group branch -- which no sweep covers, because
`router/sweep_stale_worktrees.py` reclaims *task* branches and quarantined groups' worktrees -- is
removed best-effort at verdict time (`push --delete` for the remote branch plus a local
`branch -D`), mirroring `verify.cleanup_group`'s own best-effort pair (`verify.py:1796-1810`) and
under the same shared git lock the integration already holds. Deleting it loses nothing by
construction (its content is contained in base, or it has no commits beyond it), and a dependent
group stacking on it is already kept safe by `dependency_start_ref`'s base-containment pruning
(`live.py:2149-2156`). A deletion failure is logged and never turns the verdict back into a
quarantine.

## Testing

Real-git fixtures, no network. `tests/orchestrator/test_integrate.py` (beside
`DeliverableAlreadyInTargetTests`, which already builds the squash shape) covers, per seam: a reuse
path whose remote branch is an ancestor of base records MERGED with an empty `pr_url`, no
`quarantine_reason`, and no `gh pr create` in the recorded calls; the same for a branch that was
pushed and then collapsed onto base by the rebase retry; a reused branch with real unshipped work
still opens a PR unchanged; a merge conflict whose branch is conclusively contained records MERGED,
while a conflict whose branch carries content base lacks still quarantines with
`QUARANTINE_MERGE_CONFLICT`; a stacked task branch with no commits of its own beyond a landed
dependency start ref records MERGED without a merge attempt, while the same shape with the start
ref's content absent from base takes the ordinary path; the branch-containment check concludes
nothing when the PR base ref does not resolve; and the no-op-delegate fixture (`test_branch_with_no
_commits_stays_a_true_no_op`) still classifies as not-delivered.
`tests/orchestrator/test_stacked_worktree_squash_merged_dependency_branch.py` covers the
conclusive-containment variant directly: the squash shape and the review-squash conflict shape both
still read as contained at the default, while the conflicted shape reads as *not* contained with the
shortcut disabled. `tests/orchestrator/test_live_tail_reconciliation.py` covers the tail half
end-to-end through `reconcile_unreconciled_tail_evidence`: a stacked verification-only finding is
enriched `reconcile_state == "merged"` and the run-complete note reports no unreconciled evidence
for it, where the pre-fix flow records `quarantined`.

The verification task re-drives the cited run's shape as a scratch-repo end-to-end: a group branch
contained in base, a resumed integration over it, and an assertion that the group reaches the
terminal delivered state with no quarantine record and no retained branch.
