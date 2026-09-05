# review-loop-convergence Specification

## Purpose
Bounded convergence for the orchestrator review loop: re-review rounds carry the prior round's findings forward so reviewers reconcile old issues before adding new ones, and an escalated task's journal entry records every review round.
## Requirements
### Requirement: Re-review rounds carry the prior round's findings forward

`dispatch.apply_report` SHALL, whenever it applies a `ROLE_REVIEW` report that carries a
`review_status`, additionally stash that report's `critical_issues`, `major_issues`, and
`notes` onto the task under `review_critical_issues`, `review_major_issues`, and
`review_notes`. When `build_worker_prompt(ROLE_REVIEW, task, ...)` is called for a task
whose `retry_count` is greater than 0, the rendered prompt SHALL name the round number
and the stashed prior-round `critical_issues`/`major_issues`/`notes`, and SHALL instruct
the reviewer to state, for each previously-reported issue, whether it is now Resolved or
Still Present before listing any new finding. A review dispatched at `retry_count == 0`
SHALL render exactly as before this change — no round-awareness text.

#### Scenario: First review round is unaffected

- **WHEN** `build_worker_prompt(ROLE_REVIEW, task, ctx)` is called for a task with
  `retry_count` absent or 0
- **THEN** the rendered prompt contains no round-number or prior-findings text

#### Scenario: Second review round names the prior round's findings

- **WHEN** a task's first review reported `critical_issues: 1`, `major_issues: 2`,
  `notes: "missing null check in parse()"`, `apply_report` applied that report, and
  `build_worker_prompt(ROLE_REVIEW, task, ctx)` is then called for the same task
  (`retry_count` now 1)
- **THEN** the rendered prompt names round 2, states 1 critical and 2 major issues from
  the previous round, includes the prior `notes` text, and instructs the reviewer to
  mark each as Resolved or Still Present before listing new findings

#### Scenario: A PASSED or skipped review does not seed round-awareness on a later restart

- **WHEN** `apply_report` applies a `ROLE_REVIEW` report with `review_status: "PASSED"`
- **THEN** the task's `review_critical_issues`/`review_major_issues`/`review_notes` are
  still stashed from that report (a PASSED report's own counts, typically 0), consistent
  with every `ROLE_REVIEW` report being stashed regardless of verdict

### Requirement: An escalated task's journal entry records every review round

When the review 3-strike circuit breaker fires — `dispatch.apply_report` returns
`"escalated"` for a `ROLE_REVIEW` report — the journal entry that report produces SHALL
carry a `convergence_summary` list with one item per review round already recorded for
that task in this run's journal, each item giving that round's `review_status`,
`critical_issues`, `major_issues`, and `notes`, in round order, ending with the
escalating report itself as the final round.

#### Scenario: Escalation entry lists all three rounds

- **WHEN** a task's three review rounds report `FAILED`/`FAILED`/`FAILED` and the third
  trips the circuit breaker
- **THEN** the journal entry for that third report has `convergence_summary` with three
  items in order, the third matching that report's own `review_status`/`critical_issues`/
  `major_issues`/`notes`

#### Scenario: A non-escalating entry has no convergence_summary

- **WHEN** a review report's `review_status` is `FAILED` but the task's retry count after
  the transition is below `MAX_REVIEW_RETRIES`
- **THEN** the journal entry for that report has no `convergence_summary` key

#### Scenario: A fix role's terminal failure does not get a convergence_summary

- **WHEN** a `ROLE_FIX` report has `status: "failed"` (routing to the task's terminal
  `"failed"` status, not the review circuit breaker)
- **THEN** the journal entry for that report has no `convergence_summary` key
