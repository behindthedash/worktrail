## Context

See proposal.md for the incident and the four existing pieces. The mechanics a recovery command
must respect, verified in this checkout:

- A resumed run re-integrates a group by assembling a group branch in a throwaway worktree
  started at the current base and merging each deliverable task branch into it
  (`integrate.py`'s `_integration_worktree` / `merge --no-edit <spec_id>/<task_id>`; the
  already-pushed-branch path just refetches). A task branch forked before the base advanced
  conflicts at that merge, which is why a resume of an unrepaired quarantine quarantines again.
- A failed task's journal entry is cleared — not its retained branch. `clear_tasks`
  (`live.py:1596`) removes failed/escalated entries, cascades to dependency-gate entries they
  blocked, refuses when a completion record exists, and refuses when a named task has nothing to
  clear. On replay, a previously-successful role entry is kept, so the resumed task continues on
  its retained worktree and branch rather than from scratch — the branch the operator must fix.
- `resume_group.select_groups`/`clear_groups` (`resume_group.py`) define the group half: pop the
  record, drop `integrate_complete`, append a `resumed_quarantines` entry (group, prior reason,
  detail, pr_url, cleared_at), refuse a named group that is not QUARANTINED, and refuse when a
  live run holds the journal's RunLock.
- `quarantine_selfcheck.group_task_ids(repo, spec_id, group)` re-partitions the spec's cached
  RunPlan and returns a group's task ids, or `None` on missing/unreadable cache or plan drift.
- `worktrail-live`'s `main()` resolves default models only for `_SPAWNING_SUBCOMMANDS`; a
  non-spawning subcommand must not be gated on a routing table (the `precheck` regression).

## Goals / Non-Goals

**Goals:**

- One bounded command for the documented loop: validate, repair retained task branches, clear
  the journal once, print the resume invocation.
- Fail closed: any conflict, refusal, or live-run lock leaves the journal byte-identical and
  the recovery re-runnable; no half-cleared journal is reachable.
- Reuse `clear_tasks`, `resume_group`'s clearing semantics, and `group_task_ids` — one
  implementation of each guardrail.
- Surface exactly what blocked a repair (the conflicted paths), because that is the step the
  manual loop is worst at.

**Non-Goals:**

- Auto-resolving conflicts (no worker spawn, no strategy flags); a human or agent resolves.
- Repairing the retained *group* branch: a pushed group branch is the verify chain's resolve
  worker's job (it already merges base into the branch and pushes), and an unpushed one is
  reassembled from current base by integration anyway.
- Launching `full-real --resume` from the recovery process (see Decisions).
- A bulk sweep (`--all-resumable` analogue), retrying tasks, or changing quarantine policy.
- Re-pointing `worktrail-quarantine-selfcheck`'s recovery hint; a follow-up can.

## Decisions

- **Repair before clearing, one write, lock re-checked at write time.** Journal changes are
  composed in memory (clear-task semantics + group-record pops + audit) and written with the
  single `progress.atomic_write_text` call, after every branch repair has succeeded, with the
  RunLock re-checked immediately before the write. Clearing first and repairing after was
  rejected: a later conflict would leave entries cleared with the branch still stale, i.e. the
  resume would re-quarantine — the exact loop this change removes. Two separate writes
  (`clear_tasks` then a group clear) were rejected because a crash between them recreates the
  half-recovered journal the operator currently has to reason about by hand.

- **Merge the current base into the retained task branch; never rebase, never push.** The merge
  mirrors the resolve worker's own instruction ("fetch, merge `remote/base` into the branch")
  and cannot rewrite commits a downstream branch may already be stacked on. A rebase was
  rejected for that history rewrite; rebuilding the branch from base was rejected because it
  discards the retained work the recovery exists to preserve. The merge commit is local (task
  branches are not pushed); integration reads it locally on the resume.

- **A conflict aborts the merge and reports the paths.** Leaving the worktree mid-merge was
  rejected: the resumed worker would start on top of an in-progress conflicted merge. After
  `git merge --abort` the branch is byte-identical, the conflicted paths are captured before the
  abort, and the operator fixes the branch (by hand, or via the same resolve surface the run
  uses) and re-runs — the ancestor check then short-circuits and the clear proceeds.

- **Repair runs in the branch's existing checkout.** The retained task worktree is located via
  the existing checkout-on-branch lookup; when the branch exists with no checkout (e.g. a swept
  worktree), a throwaway worktree is created for the repair and removed after, mirroring
  `_integration_worktree`'s scratch pattern. A checkout with uncommitted changes refuses the
  whole recovery: that dirt is the retained-work salvage paths' business, and merging around it
  (or stashing it) would hide work the operator may still need.

- **Group selection resolves through the cached RunPlan, and refuses when it cannot.** Journal
  entries carry task ids but no group name, so `--group` resolution uses
  `quarantine_selfcheck.group_task_ids` (the same recomputation the visibility and reclaim
  specs already trust). `None` refuses with a diagnostic naming the group; guessing from branch
  names or entries was rejected as exactly the silent mismatch the quarantine tooling avoids
  elsewhere. `--tasks` remains the explicit escape hatch, and covers plain failed tasks that
  are not in any quarantined group.

- **Print the resume invocation, never launch it.** `full-real` started inside the recovering
  session's process tree is reaped mid-run on this fleet; launching from recovery would inherit
  that and would also make recovery unbounded. The printed command matches
  `worktrail-resume-group`'s established `next:` contract, and operators who want a supervised
  launch already have `worktrail-detach launch`.

- **`recover.py` module plus a thin subparser in `live.py`.** `live.py` is already ~8k lines and
  `resume_group.py` is the precedent for a focused recovery module; the subparser, its help
  text, and the dispatch branch stay in `live.py` next to `skip`/`clear-task`. The subcommand is
  added to the non-spawning set, so it resolves no default model.

## Risks / Trade-offs

- [A clean textual merge can still be semantically wrong] → the resumed run's ordinary review,
  CI, and verify chain judges the resulting PR; recovery preserves both sides' content and
  claims no verdict, exactly like the other salvage paths.
- [Recovery mutates branches even though the journal is untouched on failure] → merges are
  reported (task, resulting commit) and idempotent; `--dry-run` reports the plan without
  mutating anything, and a clean merge on a branch is precisely what the resume needs.
- [A live run grabs the RunLock mid-repair] → the pre-write re-check refuses the journal write;
  repaired branches are harmless to the live run and already-merged branches stay applied.
- [A missing/unreadable RunPlan cache blocks `--group` recovery] → the diagnostic names the
  group and the remedy (compile the plan, or pass `--tasks` explicitly).
- [Merging base into a branch whose failure was unrelated to staleness] → harmless and
  integration-ready by construction; the task's own retry/review behavior is unchanged.

## Migration Plan

None. A new subcommand and one reference edit; no configuration, journal schema, or data
migration. Rollback is a code revert; quarantined groups and run journals are untouched by a
rollback, and every recovery action is visible in `resumed_quarantines`, the cleared-entries
summary, and the reported merge commits.
