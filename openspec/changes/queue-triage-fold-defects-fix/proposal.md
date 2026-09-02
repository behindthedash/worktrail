## Why

PR #898 (closed unmerged 2026-09-02) applied a `fold-into-change` verdict
for brief `20260831-060019-stale-bookkeeping-worktrail`, folding it into
`stale-sweep-reconciled-task-exemption` on a focus-overlap score of only
0.43 — effectively a title-substring match ("stale bookkeeping") — while
the brief's actual underlying drift finding (`codex-api-auth-lane`) had
already been resolved and archived by commit `e4058c7` (PR #875,
2026-08-31) before the fold PR was even opened. Tracing the fold/apply path
in `src/worktrail/workqueue/queue_triage.py` surfaces three independent
defects behind that outcome:

1. `rank_change_candidates()` presents every active change to the
   evaluator regardless of score, with no minimum-confidence floor. A
   weak, effectively-coincidental lexical match is presented on equal
   footing with a strong one, and nothing stops the evaluator from folding
   into it.
2. `_apply_fold_into_change()`'s `prepare()` (queue_triage.py:1598) writes
   `v.evidence` — free-form evaluator prose, which can span multiple
   lines — directly as the text of a single `- [ ] N.1 <evidence>` task
   checklist item. A multi-line or otherwise unsanitized evidence string
   breaks the one-line-per-task convention `tasks.md` checklists (and
   `overlap_check._parse_openspec_tasks()`) depend on.
3. `_worktree_pr_close()` (shared by fold and propose) runs
   `git worktree add -b <branch> <dir> <base_branch>` straight off the
   caller's local checkout with no preceding `git fetch`. When that local
   checkout is behind `origin` — as is normal for a long-lived automation
   checkout between runs — the fold worktree is built from stale state,
   so a target that was archived upstream in the meantime is still read
   as active locally, and the resulting PR opens against out-of-date
   content. This is consistent with PR #898 having been opened against a
   target whose real-world relevance had already moved on and then closed
   unmerged.

None of this brief's listed candidate changes
(`tail-dispatch-noop-and-pr-discovery-guard`, `work-queue-dependency-diagnostics`,
`managed-codex-probe-contract`, `work-queue-conservative-dependency-resolution`,
`work-queue-corpus-canonical-style-scan`) touch `queue_triage.py`'s
fold/candidate-ranking logic, so this needs its own change.

## What Changes

- `rank_change_candidates()` gets a minimum-score floor (0.45, matching the
  existing "strong overlap" bar already documented in the `intake-triage`
  spec's own example scenario): a change scoring below the floor is never
  included in the candidates presented to the evaluator, so a
  title-substring-level coincidence can no longer become a `target_change`.
- `_apply_fold_into_change()`'s `prepare()` collapses `v.evidence` to a
  single line (internal newlines/whitespace normalized) before writing it
  as a `tasks.md` checklist item's text; the `proposal.md` "Folded from"
  section, which is prose and not a checklist line, keeps the evidence
  verbatim.
- `_worktree_pr_close()` runs `git fetch origin <base_branch>` before
  `git worktree add`, and branches the fold/propose worktree off
  `origin/<base_branch>` instead of the caller's local branch ref, so the
  fold/propose worktree — and the file-existence check `prepare()` already
  performs inside it — reflects the true current state of the target repo
  and fails closed for a target archived upstream since the last local
  fetch, instead of silently working from stale state. This applies to
  both `fold-into-change` and `propose-change`, since both share
  `_worktree_pr_close()`.

## Capabilities

### Modified Capabilities
- `intake-triage`: `Candidate targets are ranked brief-to-active-change`
  gains a minimum-score floor below which a change is never presented as a
  fold candidate; `Fold and propose are applied as a pull request,
  fail-closed` gains a fetch-before-branch requirement and a single-line
  requirement for evidence written into a target change's `tasks.md`.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`rank_change_candidates`,
  `_apply_fold_into_change`'s `prepare`, `_worktree_pr_close`)
- `tests/workqueue/test_queue_triage.py`
