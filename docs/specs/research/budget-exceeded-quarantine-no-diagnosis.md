# Investigation: budget_exceeded quarantine branch gives no diagnosis of which tasks were stuck

Route I investigation, brief `20260824-180046-worktrail-orchestrator-the-fan-out`.
Continued into Route F in the same run (root cause confirmed, fix is small and
clearly in scope).

## Verified Observations

- `_pipeline_scheduler` (`live.py`) has a single post-fanout loop
  (`live.py:4790-4824`) that quarantines every group not yet in
  `dispatched_groups`, branching three ways:
  - `if budget_exceeded:` (`live.py:4793-4802`) — sets
    `quarantined[gname] = "fan-out incomplete (run budget exceeded)"`, a
    static string with no reference to which task(s) in the group were
    actually stuck.
  - `elif _group_is_terminal(g):` (`live.py:4803-4808`) — the group finished;
    dispatched to integrate/verify normally.
  - `else:` (`live.py:4809-4824`) — the non-budget "fan-out incomplete (run
    budget or error)" quarantine. PR #688 added a call to
    `diagnose_stuck_group(g, by_id, terminal_statuses)` here and appends its
    result to the quarantine reason when non-empty.
- `diagnose_stuck_group(g, by_id, terminal_statuses)` (`live.py:864`) takes
  only the group dict, the run's `by_id` task map, and the terminal-status
  set. It does not read or depend on `budget_exceeded` in any way — it walks
  each non-terminal member's unmet-dependency chain to its root(s) and names
  tail-kind roots specifically. It is equally applicable to a group that is
  non-terminal because the budget ran out mid-fan-out as to one that is
  non-terminal for any other reason.
- Both `by_id` and `terminal_statuses` are already in scope at
  `live.py:4793` — `by_id` is assigned at `live.py:4100` and
  `terminal_statuses` at `live.py:4229`, both inside the same enclosing
  `_pipeline_scheduler` function, well before the post-fanout loop.
- `tests/orchestrator/test_pipeline_budget_partial_group.py` already
  constructs the harness needed to exercise this branch directly:
  `_run()` calls `live._pipeline_scheduler(...)` with a fake spawn/integrate/
  verify and asserts on `result["quarantined"][<group>]`.
- `worktrail-check-brief-staleness` flagged commit `1615b86` (PR #688) as a
  possible match for this brief (it touches `live.py` and
  `diagnose_stuck_group`). Confirmed by inspection: PR #688 only wired the
  helper into the `else` (non-budget) branch above; the `if budget_exceeded:`
  branch is untouched by it and still has no diagnosis. The brief's request
  is not satisfied by that commit — it is the sibling fix the brief itself
  cites as prior context for a different call site.

## Unknowns / Missing Evidence

None — the fix is a direct, general reuse of an already-tested helper at a
second call site with all the inputs it needs already in scope.

## Confirmed Root Cause

The `budget_exceeded` quarantine branch (`live.py:4793-4802`) never calls
`diagnose_stuck_group()`, so its message is always the static string
`"fan-out incomplete (run budget exceeded)"` regardless of what actually
blocked the group's fan-out. A human reading the message cannot distinguish
"this group genuinely just needed more `run_budget`" from "this group is
stuck on the same kind of dependency deadlock (e.g. a tail-kind blocker)
that `run_budget` alone will never fix" — exactly the diagnostic gap PR #688
closed for the sibling non-budget branch, left open here because that PR's
scope was the other call site.

## Recommended Next Route

Route F (defect repair) — continued in this run. Fix: call
`diagnose_stuck_group(g, by_id, terminal_statuses)` in the `budget_exceeded`
branch too, and append its result to the quarantine reason when non-empty,
mirroring the non-budget branch's existing pattern.
