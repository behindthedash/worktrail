## Why

`_ROLE_ACTION[ROLE_REVIEW]` (`src/worktrail/orchestrator/dispatch.py:319-331`) has the
review worker run `git diff {base_commit}..HEAD` and review it against the task's AC —
every round, from a cold start. `transition()` (`dispatch.py:1126-1159`) already bounds
the review/fix cycle to `MAX_REVIEW_RETRIES = 3` strikes before returning `"escalated"`
(`dispatch.py:90,1150-1151`), so the loop is bounded today; what it lacks is memory. A
round-2 or round-3 reviewer is handed the same blind prompt as round 1: nothing tells it
this is a retry, and nothing carries forward what the previous round found. Two
consequences follow directly from that:

- The reviewer cannot distinguish "the same defect I flagged last round is still there"
  from "here's an unrelated new nitpick" — it has no prior findings to reconcile against,
  so a task can burn all three strikes on three different complaints instead of
  converging on the one the fix worker was actually asked to address.
- `{spec_folder}reviews/{task_id}-review.md` is overwritten each round (never versioned),
  and the report-back JSON carries only `critical_issues`/`major_issues` counts, not
  finding text. By the time a task escalates, the journal's `escalated` entry
  (`_apply_step_commit`, `live.py:3768-3834`) reflects only the report that tripped the
  breaker — round 1 and round 2's counts/notes are scattered across earlier journal
  entries an operator has to hunt down by hand to tell whether the loop was thrashing
  (different findings each round) or stuck (one finding never resolved).

Grep confirms no existing mechanism addresses either gap: `review_verdict_rule` is the
only review-loop hit for `declined`/`round_cap`/`max_round`/`prior round` in
`dispatch.py`/`live.py` (the actual round cap is `MAX_REVIEW_RETRIES`, an unrelated
string). No in-flight change touches this — `shared-pr-landing-pipeline` covers PR
landing, not review-loop convergence.

## What Changes

- **Re-review rounds carry the prior round's findings forward.** `dispatch.apply_report`
  additionally stashes the just-applied review report's `critical_issues`,
  `major_issues`, and `notes` onto the task dict whenever `role == ROLE_REVIEW`. When
  `build_worker_prompt(ROLE_REVIEW, ...)` is called for a task whose `retry_count > 0`,
  the rendered prompt names the round number and the immediately preceding round's
  counts/notes, and instructs the reviewer to state, for each previously-reported issue,
  whether it is now resolved before listing anything new. Round 1 (`retry_count == 0`)
  is unaffected — this is additive prompt text gated on retry_count, not a rewrite of the
  existing checklist/verdict clauses.
- **Escalation records the full round-by-round history.** `_apply_step_commit`
  (`live.py`), when `dispatch.apply_report` returns the review circuit-breaker's
  `"escalated"` status, scans the run's accumulated journal `entries` for every prior
  `role == ROLE_REVIEW` entry belonging to the same task and stamps a `convergence_summary`
  list — one entry per round, each with `round`, `review_status`, `critical_issues`,
  `major_issues`, `notes` — onto the escalating journal entry, so the record that trips
  the breaker is self-contained for triage without cross-referencing earlier entries.
- **No change to the round cap itself.** `MAX_REVIEW_RETRIES` stays 3; this change makes
  the existing bound observable and gives the loop memory within it, it does not alter
  when the breaker fires.

## Capabilities

### New Capabilities
- `review-loop-convergence`: gives the orchestrator's review/fix loop memory across
  rounds — a re-review is told what the previous round found and must reconcile against
  it, and an escalated task's journal entry carries every round's findings so the
  existing 3-strike bound is diagnosable, not just enforced.

## Impact

- **Code**: `src/worktrail/orchestrator/dispatch.py` (`apply_report`, `build_worker_prompt`
  ROLE_REVIEW branch, `_ROLE_ACTION[ROLE_REVIEW]`/new round-awareness clause);
  `src/worktrail/orchestrator/live.py` (`_apply_step_commit`).
- **Tests**: `tests/orchestrator/test_dispatch.py` (round-awareness clause rendering,
  `apply_report` stashing prior-round fields); `tests/orchestrator/test_live_run_circuit_breaker_terminal_status.py`
  or a sibling new test (escalation entry carries `convergence_summary`).
- **Non-goals**: no change to `MAX_REVIEW_RETRIES`, to the cumulative
  `base_commit..HEAD` diff scope (a delta-since-last-review diff would hide a fix-
  introduced regression in already-reviewed code, which the cumulative diff exists to
  catch), or to the review file's on-disk path/naming.
