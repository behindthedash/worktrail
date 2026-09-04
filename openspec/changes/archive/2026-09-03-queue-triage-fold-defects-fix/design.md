## Context

See `proposal.md - Why` for the PR #898 incident. Three call sites in
`src/worktrail/workqueue/queue_triage.py`, already traced against the
current worktree:

- `rank_change_candidates()` (~line 318): scores every active change via
  `score_candidates._overlap_coefficient()` and returns the top 5 by score,
  unconditionally — there is no floor, so a change scoring e.g. 0.43 (the
  incident's case) is presented exactly like one scoring 0.9.
- `_apply_fold_into_change()`'s `prepare()` (~line 1598): writes
  `f"- [ ] {group_number}.1 {v.evidence}\n"` — `v.evidence` is whatever
  free text the evaluator produced (per `EVALUATOR_PROMPT_TEMPLATE`'s
  `"evidence": "<cited PR/commit/file/test, ...>"` field), never validated
  or reshaped for single-line checklist use.
- `_worktree_pr_close()` (~line 1355): `git worktree add -b branch dir
  base_branch` reads `base_branch` (a short branch name from
  `_repo_base_branch()`) as a local ref with no preceding `git fetch`.

## Goals / Non-Goals

**Goals:**
- Stop a low-confidence, effectively-coincidental candidate score from
  becoming a fold target.
- Keep evidence text from corrupting a target change's `tasks.md`
  checklist formatting.
- Make the fold/propose worktree reflect the true current state of the
  target repo at apply time, closing the race window between an
  automation checkout's last fetch and an upstream archive/merge.

**Non-Goals:**
- Re-scoring or re-ranking algorithm changes beyond adding a floor —
  `_overlap_coefficient()` itself is untouched.
- Any new explicit "is target_change still active" re-check beyond what
  `prepare()`'s existing `proposal_path.is_file()`/`tasks_path.is_file()`
  check already does; fetching fresh state before branching is what makes
  that existing check trustworthy, so no second check is needed.
- Changing `propose-change`'s own content-authoring flow — the fetch fix
  applies to it only because it shares `_worktree_pr_close()` with fold.

## Decisions

### 1. Minimum candidate score floor: 0.45, applied inside `rank_change_candidates()`

0.45 is not arbitrary: it is the same bar the `intake-triage` spec's own
"Strong overlap with an active change" scenario already uses as the
threshold for a confident match. A candidate scoring below 0.45 is dropped
from the list before the top-`top_k` truncation, so it is never presented
to the evaluator at all — `fold-into-change` can then never name it (the
existing "target must be one of the presented candidates" validation in
`_valid_verdict_fields()` already rejects any `target_change` that wasn't
presented). A repo whose every active change scores below the floor now
presents an empty candidate list, exactly like a repo with no active
changes — `propose-change`/`needs-decision` remain available.

### 2. Evidence is collapsed to one line only where it becomes a checklist item

`" ".join(v.evidence.split())` — collapses all runs of whitespace
(including newlines) to single spaces — applied only to the `tasks.md`
`- [ ] N.1 <text>` line. The `proposal.md` "Folded from" section keeps
`v.evidence` verbatim (unlimited lines are fine in prose), so no
information is lost; it is only reshaped where the one-line-per-task
format requires it.

### 3. Fetch `origin/<base_branch>` before creating the worktree, branch off the fetched ref

```
git -C <repo_path> fetch origin <base_branch>
git -C <repo_path> worktree add -b <branch> <worktree_dir> origin/<base_branch>
```
instead of branching off the bare `<base_branch>` local ref. A fetch
failure is treated like every other pre-PR step failure in
`_worktree_pr_close()`: `status="error"`, brief left untouched, branch name
reported for manual recovery. This is a two-line change inside the
existing function shared by fold and propose, not a new code path.

## Migration

None — behavioral fix, no data or interface migration.
