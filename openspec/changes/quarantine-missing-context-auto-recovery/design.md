## Context

`drive()` in `src/worktrail/orchestrator/live.py` applies each worker report
through `_commit_step`, which journals the entry and lets `dispatch.apply_report`
compute the next status. The only existing `missing_context` consumer on the
failure path is `_scope_escalation_files`, which validates paths as existing,
non-ignored, non-colliding files in the task worktree and widens scope once.
A path that is *absent* from the worktree is discarded there, so a worker that
correctly reports "sibling's file is not here" drives the task to `failed`, the
group cannot reach terminal, and it is quarantined at the fan-out boundary.

The hand recovery works because it forces a fresh stacked worktree fork from
the live base, where the sibling's merged content now exists. The orchestrator
already has every primitive for that: `_live_base_ref`, `git worktree remove
--force` (used by the one-shot runner), `add_stacked_worktree` via `ensure_wt`
on a non-existent path, and the pending frontier.

## Decisions

### D1. Trigger is evidence-based and narrow

Recovery is evaluated only when a report would otherwise land the task in
`failed` or `escalated`, and only for `missing_context` paths that satisfy all
of:

1. repo-relative, no whitespace, resolves inside the worktree;
2. declared in `files` of at least one *other* task in `by_id` (same RunPlan),
   whether or not that task is in flight;
3. not present as a file in the task's worktree;
4. present on the live base ref (`_live_base_ref(repo, remote, base)`) with a
   non-empty blob (`git cat-file -e` and `-s` on `<base>:<path>`).

Condition 2 keeps this from firing on typos or unrelated repo files. Condition 3
makes it disjoint from scope escalation by construction. Condition 4 is the
"has since merged" evidence; a sibling that is still in flight or unmerged
does not qualify and the existing failure path applies. When the base ref
cannot be resolved (offline, no such branch) recovery does not fire.

### D2. Recovery replays the manual sequence, in-process, once

When at least one path qualifies:

- append an `event: missing_context_auto_recovery` journal entry with
  `task`, `paths`, `sibling_tasks`, `base_ref`, `base_sha`, the triggering
  `role`, and `category: orchestrator_defect`, then persist;
- remove the task worktree (`git worktree remove --force`) and delete its
  branch, under the shared git lock;
- reset `status` to `pending`, `retry_count` to 0, drop `_extra_reads`,
  `_scope_*` state, and set `_missing_context_recovered = True`;
- do **not** stamp `terminal_status` on the triggering report's own entry; it
  is journaled with `auto_recovered: true` for audit so `clear_tasks()` sees
  nothing terminal to refuse or remove.

The frontier scheduler then re-dispatches the task like any pending task, and
`ensure_wt` creates a fresh stacked worktree from the current base. A second
qualifying report on the same task in the same run is applied as an ordinary
failure: a genuinely missing file after a fresh fork is a real problem and
must surface to a human.

### D3. Precedence and interaction with existing paths

Recovery is checked before scope escalation. If recovery fires, escalation
and adaptive read-widening for that report are skipped; the fresh worktree
makes them moot. If recovery does not fire, the existing D5 escalation logic
runs unchanged. Quarantine write sites are untouched: a recovered task is
simply non-terminal and pending, so `_group_is_terminal` and the budget-
exhausted quarantine behave as they do for any in-flight task.

### D4. Resume fidelity

Journal replay must apply the event the same way a live run did: on seeing a
`missing_context_auto_recovery` event the replayer resets that task to
pending, clears strikes, and marks it recovered so the once-only guard holds
across a resume. The triggering entry, lacking `terminal_status`, replays as
non-terminal.

## Risks / Trade-offs

- **Content conflict on fork.** The fresh worktree may carry the task's own
  earlier commits if its branch is reused; deleting the branch is what
  guarantees a clean fork from base, at the cost of discarding the failed
  attempt's partial work. That matches the manual procedure and the report
  entry keeps the record.
- **Non-empty blob is a weak content check.** A sibling that merged a stub
  would still qualify, but the re-dispatched worker then fails against real
  content and surfaces normally on the second attempt.
- **Three report-application sites.** `live_run`'s own pre-`_commit_step` path
  is a one-shot/replay surface and is deliberately left out; only the two
  `drive()` sites that call `_commit_step` are wired, and the structural test
  names them so a future third site is not missed silently.
