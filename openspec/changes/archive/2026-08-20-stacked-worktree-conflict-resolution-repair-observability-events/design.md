## Context

`src/worktrail/orchestrator/live.py` already has one precedent for this exact
pattern: `_require_dependency_files` fires a structured `dependency_file_drift`
journal event (in addition to its `WARN:` print) whenever it detects
declared-vs-actual filename drift, and both call sites
(`ensure_wt`/`_ensure_wt`) already extend `entries` with whatever event list
they get back and call `record()`. `safety_net_report.py` aggregates
`dependency_file_drift` fire counts across every run journal for a repo.

PR #554 added two further repair paths in the same file that engage under
comparable "recovered instead of failing hard" circumstances, but neither
returns or journals anything:

- `_carry_squash_merged_dependencies` (line ~1724): when a squash-merge carry
  conflicts and the conflict is confined entirely to the change's own
  `openspec/changes/<id>/tasks.md`, `_resolve_tasks_md_checklist_conflict`
  resolves it via checklist union and the function returns `None` -- no
  `WARN:`, no event. This is currently the *most* silent of the three: even
  the human-visible log line is absent on this path.
- `_require_dependency_files_with_repair` (line ~2826): on
  `WorktreeMissingDependencyFileError` for a *retained* (resumed) task
  worktree, it re-attempts the carry once and re-validates. If the retry
  resolves the drift, nothing distinguishes this from the fresh-worktree,
  no-drift path in the journal -- there is no `worktree_drift_repaired`
  counterpart to `dependency_file_drift`.

Both are exactly the class of event `safety_net_report.py` exists to
aggregate (see its own module docstring): "took the safety net" vs. "used the
normal path". Brief 20260817-223443 needed two separate live incidents
(2026-08-17, 2026-08-19) weeks apart before anyone connected the pattern,
because nothing let a review ask "how often does this recur" without grepping
raw run-journal files.

## Goals / Non-Goals

**Goals:**
- `_carry_squash_merged_dependencies` reports whether it resolved a checklist
  conflict via the union-merge path, instead of being a `-> None` function.
- `_require_dependency_files_with_repair`'s retained-worktree repair retry
  fires a `worktree_drift_repaired` event when the repair resolves the drift
  (the second `_require_dependency_files` call succeeds), and folds in a
  `checklist_conflict_resolved` event when the carry took that path -- both
  via the existing `drift_events` return-list convention its two call sites
  (`ensure_wt`, `_ensure_wt`) already journal generically.
- `safety_net_report.py` aggregates both new event types the same way
  `dependency_file_drift` is aggregated: a fire count under `by_event`, plus
  a `checklist_conflict_resolved`/`worktree_drift_repaired`-by-task
  breakdown mirroring `dependency_file_drift_by_dep_id`.

**Non-Goals:**
- Journaling `_carry_squash_merged_dependencies`'s checklist-union outcome at
  its OTHER call site, inside `add_stacked_worktree` (fresh worktree
  creation, line ~1880). `add_stacked_worktree` is a `-> None` function
  exercised directly by tests/mocks outside this closure's journal plumbing;
  widening its return contract to thread events back to `ensure_wt`/
  `_ensure_wt` is a materially larger, differently-scoped change than what
  the two cited live incidents (both retained-worktree RESUME repairs, per
  brief 20260817-223443) actually need. Deferred: different purpose from this
  change's brief, not a correctness gap it introduces.
- Modeling this as an OpenSpec requirement change. Neither
  `dependency_file_drift` nor `safety_net_report.py`'s aggregation is spec-
  documented today (`skip_specs: true` on this change, matching that
  precedent) -- the merge/resolve SHALL-level behavior of both repair paths
  is unchanged; this is an observability side effect only.
- The third PR #554 change (stamping `terminal_status` for a normal "failed"
  transition report in `_apply_step_commit`/`drive()`). That is a one-time
  correctness fix so `clear_tasks()` recognizes an existing status value --
  not a recurring silent recovery path with its own failure mode to track.

## Decisions

- **Event shape mirrors `dependency_file_drift`**: `{"event": "<name>",
  "task": task["id"], "at": round(time.time(), 3)}`, plus event-specific
  fields. `worktree_drift_repaired` adds no extra field (the repair is
  binary: it either resolved the drift or the exception still propagates).
  `checklist_conflict_resolved` adds no extra field either (the file it
  resolves is always `openspec/changes/<change_id>/tasks.md` by construction
  of `_resolve_tasks_md_checklist_conflict`'s own confinement check).
- **`checklist_conflict_resolved` only surfaces on the repair path's overall
  success.** `_carry_squash_merged_dependencies` returns the event dict (or
  `None`) to its caller; `_require_dependency_files_with_repair` holds it and
  only appends it to the events it returns if the subsequent
  `_require_dependency_files` re-check does not raise. If the retry still
  raises, the exception propagates immediately (unchanged behavior) and no
  event is journaled for that attempt -- consistent with `dependency_file_drift`
  itself only firing on a call that returns normally, never partway through
  a raise.
- **`safety_net_report.py` breakdown key is `task`, not a dependency id.**
  `dependency_file_drift_by_dep_id` breaks down by *which dependency* keeps
  drifting because that is the actionable unit (fix that dependency's
  frontmatter). Neither new event has an equivalent per-dependency axis
  (`worktree_drift_repaired` is per-worktree; `checklist_conflict_resolved`
  always targets the same checklist file) -- the useful axis is *which task*
  keeps needing the repair, so both new breakdowns key on `task`.

## Risks / Trade-offs

- Extra journal writes on an already-locked path (`state_lock` is already
  held by both call sites for the existing `drift_events` extend) -- no new
  locking, same `entries.extend()` + `record()` shape.
- Test coverage for `_carry_squash_merged_dependencies`'s new return value
  must not regress its existing callers that treat the return as unused
  (`add_stacked_worktree`'s call site keeps discarding it, so a `None`
  return there stays valid Python either way).
