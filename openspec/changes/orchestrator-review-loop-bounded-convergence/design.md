## Context

See proposal.md — Why. This records the two hook points verified in this worktree.

- `dispatch.transition` (`dispatch.py:1126-1159`): on `ROLE_REVIEW` with `review_status ==
  "FAILED"`, increments `retry_count`; at `retry_count >= max_retries` (default 3)
  returns `("escalated", retry_count)`, else `("fixing", retry_count)`.
- `dispatch.apply_report` (`dispatch.py:1162-1181`): looks up the task by id, calls
  `transition`, sets `task["status"]`/`task["retry_count"]`, and — only for `ROLE_REVIEW`
  with a `review_status` present — sets `task["review_status"]`. This is the single
  place that already threads a review verdict onto the task dict across rounds; the
  round-awareness stash (below) is a same-shaped addition here, not a new mechanism.
- `live.py`'s `LiveSpawn.__call__` (`live.py:2777-2817`) builds the `ctx` dict passed to
  `dispatch.build_worker_prompt` fresh on every dispatch, from `task` and run-level state.
  It already reads `task` fields (e.g. `task.pop("_extra_reads", ...)` for adaptive read-
  widening on fix dispatches) — the round-awareness clause reads task fields the same way,
  no new ctx field needed on `WorkerPromptCtx`.
- `live.py`'s `_apply_step_commit` (`live.py:3768-3834`) is the single choke point for
  every journal entry, review or otherwise (its own docstring: "Append the journal entry
  ... apply the state transition"). It has `entries` (this run's accumulated journal list)
  and `task` in scope, and already special-cases `new in ("escalated", "failed")` to stamp
  `report_fields["terminal_status"]`. The `convergence_summary` stamp is added in that
  same `if new in (...)` block, scoped further to `new == "escalated"` — the review-only
  circuit breaker, not the fix-role terminal "failed" case sharing that branch.

## Goals / Non-Goals

**Goals:**
- A round-2+ review prompt names the round number and reconciles against the immediately
  preceding round's findings before listing new ones.
- An `escalated` journal entry is self-contained: every round's `review_status` /
  `critical_issues` / `major_issues` / `notes` for that task, in order, without requiring
  a scan of earlier journal entries to reconstruct what happened.
- No behavior change to round 1 (`retry_count == 0`) reviews, to `PASSED`/`SKIPPED-SMALL-DIFF`
  verdicts, or to the fix role.

**Non-Goals:**
- Changing `MAX_REVIEW_RETRIES` or the escalation trigger condition.
- Switching the review diff from cumulative (`base_commit..HEAD`) to delta-since-last-
  review — the cumulative diff is what lets a round-2 reviewer catch a regression the
  fix introduced in code a prior round already passed; narrowing it would remove that
  safety property, not fix the memory gap.
- Persisting full review-finding *text* across rounds. The report-back schema carries
  only `critical_issues`/`major_issues` counts and free-text `notes`
  (`dispatch.py:744-746`); this change threads those existing fields forward rather than
  adding a new structured-findings schema, which would touch every review worker's
  report-back contract for a benefit (verbatim finding text in the next prompt) the
  counts + notes already give the reviewer enough to reconcile against.
- Versioning `{task_id}-review.md` on disk (e.g. per-round filenames). The shared-file
  (OpenSpec) review-verdict clause never instructs the reviewer to commit that file (only
  the own-file/devkit clause does — `_REVIEW_VERDICT_OWN_FILE` vs
  `_REVIEW_VERDICT_SHARED_FILE`, `dispatch.py:506-521`), so a prior round's on-disk text
  is not reliably recoverable via git history in OpenSpec-format runs (this repo's own
  format). The journal is the reliable cross-round record; this change uses it rather
  than adding a commit requirement to a role whose commit contract this change does not
  otherwise need to touch.

## Decisions

### D1. Stash prior-round fields on the task dict, in `apply_report`

`apply_report` already sets `task["review_status"]` for `ROLE_REVIEW`. Extend the same
block to also set `task["review_critical_issues"]`, `task["review_major_issues"]`,
`task["review_notes"]` from the report whenever they're present. These are plain
overwrites (round N's stash replaces round N-1's) — the prompt only ever needs the
*immediately preceding* round to ask "is this resolved now", and the full round-by-round
history for escalation triage comes from the journal (D3), not from the task dict.

Alternative considered: thread a new field through `WorkerPromptCtx`/`LiveSpawn.__call__`
instead of the task dict. Rejected — `task` is already the vehicle for cross-round state
(`retry_count`, `review_status`), and `build_worker_prompt` already takes `task` as a
parameter; adding another ctx field for data that's naturally per-task, not per-dispatch,
would be the odd one out next to the existing pattern.

### D2. Round-awareness clause renders only when `retry_count > 0`

`_ROLE_ACTION[ROLE_REVIEW]`'s `.format(...)` call in `build_worker_prompt` already
receives `task_id`/`base_commit`/`spec_folder`/`task_brief`/`review_checklist`/
`review_verdict_rule` as format args. Add one more computed value,
`round_awareness` — empty string when `task.get("retry_count", 0) == 0`, else a rendered
clause:

```
"This is review round {round}. The previous round found "
"{critical_issues} critical and {major_issues} major issue(s): {notes} "
"For each of those, state in this review whether it is now Resolved or Still "
"Present before listing anything new. "
```

inserted into `_ROLE_ACTION[ROLE_REVIEW]` right after the existing
`"{review_checklist}"` slot, before the `Write ... review.md` sentence — reviewing the
checklist first, then reconciling against history, then writing the verdict, matches the
order a human reviewer would actually work in.

Empty-string-when-round-1 keeps the existing round-1 golden prompt text (and any test
asserting it byte-for-byte) unchanged — this is additive, not a rewording of the current
clauses.

### D3. `convergence_summary` built from the journal's own `entries`, at the escalation site

`_apply_step_commit` already has `entries` (list) and `task` (dict with `"id"`) in scope.
When `new == "escalated"`:

```python
convergence_summary = [
    {
        "round": i + 1,
        "review_status": e["report"].get("review_status"),
        "critical_issues": e["report"].get("critical_issues"),
        "major_issues": e["report"].get("major_issues"),
        "notes": e["report"].get("notes"),
    }
    for i, e in enumerate(
        e for e in entries
        if e.get("task") == task["id"] and e.get("role") == dispatch.ROLE_REVIEW
    )
]
```

appended to include the report that is producing *this* entry (not yet in `entries` at
this point in the function — appended as the final element after the loop, same `round`
numbering). Stamped as `entry["convergence_summary"] = convergence_summary` only when
non-empty (mirrors the existing `if task.get("_scope_added_files"):` /
`if task.get("_pre_commit_restored"):` conditional-stamp style in the same function).

Alternative considered: compute this in `dispatch.transition`/`apply_report` instead,
which don't have the journal. Rejected — `transition`/`apply_report` are pure functions
over a single report and the task list; the journal (`entries`) is run-level state that
only `live.py` holds, so the aggregation belongs at the `_apply_step_commit` call site,
same reasoning as D1 keeping the per-round stash next to `apply_report`'s existing
`review_status` write rather than inventing a second place cross-round state lives.

### D4. `live_run()`'s independent journal-entry path is out of scope

`test_live_run_circuit_breaker_terminal_status.py`'s own docstring documents that
`live_run()` (the cassette/demo recording path) built journal entries independently of
`_apply_step_commit` and had to be separately fixed for `terminal_status` (PR #496/#498
dedup history). Grep confirms `live_run()` still exists as a distinct code path from the
live fan-out. This change does not extend `convergence_summary` to `live_run()` — it is a
demo/cassette recorder, not a path real runs escalate through; adding it there is a
follow-up if that path is ever found to need the same diagnostic, tracked as an Open
Question below rather than done speculatively here.

## Risks / Trade-offs

- [`review_notes` on the task dict could be a long free-text block, inflating the round-2
  prompt] → the reviewer already writes `notes` sized for a human to read in a review
  file; embedding it once in the next round's prompt is the same order of magnitude as
  the checklist/verdict-rule text already in that prompt.
- [Journal entries for a task could theoretically include a review entry from a *previous*
  run's journal if IDs were reused across runs] → journals are per-run
  (`live.py`'s run-scoped journal path), so `entries` never spans two runs; not a new
  risk this change introduces.

## Open Questions

Whether `live_run()`'s cassette/demo journal-entry path should also gain
`convergence_summary` for parity with `_apply_step_commit` — deferred; it does not affect
this change's spec or task breakdown (D4).
