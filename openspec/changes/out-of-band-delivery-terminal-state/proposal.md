## Why

An integration whose content is already on base -- delivered out-of-band by another PR (a
tail-reconciliation PR, a checkbox-sync PR, a squash-merged group PR) -- is recognized at exactly
one seam: `integrate_one`'s fresh-build empty-diff guard
(`src/worktrail/orchestrator/integrate.py:1687-1710`, backed by `_deliverable_already_in_target`
at `integrate.py:110`). Every other seam treats the same integration as undelivered and quarantines
it with a reason that misdescribes reality. Verified in this checkout, with nothing landed since
the run (`git log 424a3e38..HEAD -- src/worktrail/orchestrator/integrate.py` is empty; the file's
last touch is #1401):

- **REUSE** (`integrate.py:1644-1653`): a resume whose remote group branch already exists fetches
  it and goes `group_branch[name] = gb` (line 1799) → PR reconcile, skipping the empty-diff guard,
  the drift/smoke gates, and the push entirely. There is no delivery check on this path at all.
- **PR creation** (`integrate.py:1936-1947`): any `gh pr create` refusal -- including GitHub's
  GraphQL `No commits between <base> and <branch>` -- is quarantined as
  `QUARANTINE_INTEGRATION_ERROR`, with no content-based pre-check before the call;
  `grep -rn "No commits between" src/worktrail/` returns no match.
- **Merge conflict** (`integrate.py:1673-1677`): the merge loop's conflict branch quarantines
  `QUARANTINE_MERGE_CONFLICT` before any content-based check, even for a deliverable that carries
  nothing of its own.

The brief frames the impl-group half as "the recognition exists in the tail path and is missing for
ordinary groups". That framing is structurally off, and the correction matters for the fix:
`reconcile_unreconciled_tail_evidence` (`integrate.py:2109`) feeds every tail finding into the same
`integrate_one` seam (`integrate.py:2233`), so the recognition is *shared* and the two reported
seams are the same function reached through two entries -- the ordinary group path (REUSE → PR
create) and the tail reconciliation path (stacked branch → merge conflict). What is missing is not
a per-kind guard but the same guard at the remaining seams.

Live corroboration, run go-20261005-084258 (`full-1791215255`, change
`tail-task-auto-reconciliation-log-note-reconcile-state`; brief `20261005-105710-out-of-band-work-falsely`):

- Journal: `feature-1` at `{state: QUARANTINED, quarantine_reason: integration_error, pr_url: ""}`
  after `SKIP [feature-1] -- gh pr create failed: ... GraphQL: No commits between main and
  full-1791215255/feature-1 (createPullRequest)`. The branch is still on the remote
  (`git ls-remote origin full-1791215255/feature-1` → `29e1717980`), and in this checkout it is an
  ancestor of `main` with zero commits beyond it (`git merge-base --is-ancestor <branch> main` → 0;
  `git rev-list --count main..<branch>` → 0). The refusal was deterministic: nothing could have
  shipped from that branch, and nothing was ever undelivered.
- `tail-2.1` -- a verification-only task stacked on 1.1's branch, whose 1.1 content the target
  already carries -- was recorded `{state: QUARANTINED, quarantine_reason: merge_conflict}` and
  reported by the run-complete note as `1 tail task(s) completed with unreconciled evidence ...
  2.1 (... reconcile=quarantined)`.

Cost, both seams: a false quarantine keeps the group's worktree and branch in place for a human,
files a spurious human-review ask, quarantines dependents by `dependency_quarantined` cascade when
any exist (the mechanism the guard's own comment cites from aspens run go-20260918-193109), and
leaves the run exiting 0 after printing only `NOTE: N group(s) quarantined for human review` -- an
operator who does not read the notes believes a green run shipped work it did not. The retained
branch from the cited run is still on `origin` today.

**Premise re-verification** (brief `20261005-105710-out-of-band-work-falsely`): repo live; the three
code seams above read directly in this worktree (`integrate.py:1644-1653`, `1936-1947`,
`1673-1677`), as are the empty-diff guard (`1687-1710`), the shared reconcile entry
(`2109`, call at `2233`), and `shared/git_merged.py`'s content vocabulary (`branch_content_in_base`
at line 34, `has_commits_beyond` at line 68); the retained branch's containment in `main` is
reproduced above against the live remote; `openspec/specs/tail-task-auto-reconciliation` exists and
its "Reconciliation reuses existing conflict and quarantine handling" requirement is the text this
change amends; `tests/orchestrator/test_live_unreconciled_tail_note.py:28` carries the
unreconciled-tail note string. `ls openspec/changes/` shows no active change covering
out-of-band delivery state (the only "out-of-band" hits are `blocked-brief-auto-pick-exclusion`'s
unrelated use of the phrase). The brief's own run-path reconstruction (REUSE vs rebuild) is inferred
from control flow -- the run log/journal are not in this repo -- but the retained branch's
containment is checked live here, and every cited code gap is independent of which path the run took.

## What Changes

- **One delivered-out-of-band rule, consulted at every integration seam.** A group integration
  whose target already contains what it would ship records the terminal delivered state instead of
  quarantining, at the post-merge empty-diff seam (existing behavior, unchanged), on the
  remote-branch REUSE path, immediately before the shared PR open/update step, and on the
  merge-conflict path -- each with content-based evidence, never reading ancestry's absence as "not
  contained" (a squash merge leaves no ancestry link even though the content landed).
- **The PR-creation refusal shape is recognized before the call.** A group branch that contributes
  no commits beyond the ref its PR would target cannot produce a PR -- GitHub refuses `gh pr create`
  deterministically with `No commits between` -- so the attempt terminates as delivered without
  burning the call, and never quarantines on that refusal. A ref that does not resolve is never
  evidence of delivery.
- **A merge conflict is not evidence of delivery.** The conflict seam consults conclusive
  containment only (ancestry, or a clean three-way merge that reproduces the target's tree), so a
  branch whose changes the target does not have but which conflicts with it still quarantines with
  the merge-conflict reason exactly as today. The `branch_content_in_base` conflicted-merge
  shortcut, which the stacking and sweep call sites deliberately treat as contained, SHALL NOT
  decide a terminal verdict.
- **A stacked deliverable with nothing of its own does not attempt a merge.** A task branch whose
  own commits -- the commits beyond the dependency start ref it was stacked on -- contribute nothing
  the target lacks records the delivered/merged outcome without merging, so a verification-only tail
  task stacked on an already-landed dependency is reported as reconciled, not as an unreconciled
  merge conflict.
- **Terminal means terminal, with the existing vocabulary.** The record is the journal's existing
  `MERGED` state with an empty `pr_url`, the group branch as `head_branch`, and a console line
  naming the seam's evidence and `delivered out-of-band`; no new journal state, no quarantine
  record, no `dependency_quarantined` cascade, and no retention of the group branch for human
  review (it is removed best-effort under the integration's own git lock, failures logged).
- Non-goals: the tail-detection ledger (`detect_unreconciled_evidence` keeps flagging a task whose
  HEAD is not an ancestor of the base -- that invariant is unchanged; this change makes the
  *reconcile attempt* land the truthful outcome, not the detector); the `QUARANTINE_EMPTY_DIFF`
  no-op-delegate classification; the reuse path's skipping of the drift/smoke gates (unchanged);
  and `git_merged.branch_content_in_base`'s conflicted-merge shortcut for its existing stacking and
  sweep callers.

## Capabilities

### New Capabilities

- `out-of-band-delivery-terminal-state`: an integration whose content the target already contains
  reaches a terminal, non-quarantining delivered outcome at every seam of the shared group
  integration path (fresh build, remote-branch reuse, PR creation, merge conflict), recorded in the
  existing `MERGED` state with content-based evidence, so a delivered integration is never reported
  as an unreconciled failure or retained for human review.

### Modified Capabilities

- `tail-task-auto-reconciliation`: reconciliation still reuses the ordinary integration path's
  conflict and quarantine handling, but a reconcile attempt whose branch contributes nothing beyond
  base -- a verification-only task stacked on an already-landed dependency, or a branch whose
  content base already contains -- is recorded with the reconciled (`merged`) outcome rather than
  a merge-conflict quarantine, while an attempt whose branch carries changes base does not have is
  still quarantined with the merge-conflict reason.

## Impact

- `src/worktrail/orchestrator/integrate.py` -- the delivered-out-of-band rule and its new call
  sites (reuse path, pre-PR step, merge-conflict path) plus the stacked-own-contribution check.
- `src/worktrail/shared/git_merged.py` -- the conclusive-containment variant used for terminal
  verdicts, so the conflicted-merge shortcut stays opt-out by callers that cannot tolerate it;
  existing callers keep today's semantics unchanged.
- `tests/orchestrator/test_integrate.py`,
  `tests/orchestrator/test_stacked_worktree_squash_merged_dependency_branch.py`,
  `tests/orchestrator/test_live_tail_reconciliation.py` -- seam-by-seam coverage and the
  resume-a-contained-branch regression.
- No new console script, policy key, journal state, or dependency; `land_pr`, `verify`, and the
  run-journal readers are untouched, and no already-written `tasks.md` changes behavior.
