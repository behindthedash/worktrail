## ADDED Requirements

### Requirement: A review report can name a planner/human decision

The `ROLE_REVIEW` report-back contract SHALL accept an optional `decision_required` field:
a one-line string naming an acceptance-criterion-versus-existing-behaviour conflict that a
planner or human must resolve, citing the acceptance criterion and the conflicting test or
behaviour, or `null`. `build_worker_prompt(ROLE_REVIEW, task, ...)` for a task with
`retry_count` greater than 0 SHALL instruct the reviewer to set `decision_required` only for
a Still Present finding of that kind, never for a code defect; a round-1 review prompt SHALL
render exactly as before. `dispatch.review_names_decision(report)` SHALL return the
`decision_required` text when it is a non-empty string; otherwise it SHALL return the
report's `notes` when `notes` contains, case-insensitively, one of the phrases
`planner/human decision`, `human decision`, or `planner decision`; otherwise it SHALL return
`None`.

#### Scenario: Structured field wins
- **WHEN** a review report carries `decision_required: "AC 1.1 requires a literal exists()
  check; test_unreadable_repo requires an EACCES carve-out"` and unrelated `notes`
- **THEN** `review_names_decision` returns the `decision_required` text

#### Scenario: Phrase in notes is the fallback
- **WHEN** a review report has no `decision_required` and `notes` reads "MAJOR-1: reconciling
  the AC with the unreadable-repo requirement is a planner/human decision"
- **THEN** `review_names_decision` returns the `notes` text

#### Scenario: Ordinary findings name no decision
- **WHEN** a review report has no `decision_required` and `notes` reads "missing null check
  in parse()"
- **THEN** `review_names_decision` returns `None`

#### Scenario: Round-2 prompt explains the field, round-1 prompt is unchanged
- **WHEN** `build_worker_prompt(ROLE_REVIEW, task, ctx)` is called with `retry_count` 1
- **THEN** the prompt names `decision_required`, restricts it to Still Present
  AC-vs-existing-behaviour conflicts, and asks the reviewer to cite the AC and the
  conflicting test; with `retry_count` 0 the prompt contains no `decision_required` text

### Requirement: A repeated decision-naming finding escalates early with a pending decision

When `_apply_step_commit` applies a `ROLE_REVIEW` report with `review_status: FAILED` for
which `dispatch.apply_report` returned `fixing`, the task's post-transition `retry_count` is
at least 2, and `review_names_decision(report)` is non-`None`, it SHALL set the task's status
to `escalated` instead of `fixing`. The resulting journal entry SHALL carry
`terminal_status: escalated`, the `convergence_summary` an escalating entry already carries,
`escalation_reason: "decision-required"`, and `pending_decision`: a `worktrail.pending-decision`
envelope built by `pending_decision_envelope` with provenance `source:
"orchestrator-review-loop"`, `repo`, `subject: "<spec_rel>/<task id>"`, and the run's
`run_id`, whose `question` is the decision text and whose `decision_id` is
`decision_identity(source, repo, subject, question)`. A `FAILED` review that names a decision
on round 1 (post-transition `retry_count` 1) SHALL route to `fixing` exactly as before, and a
`FAILED` review that names no decision SHALL be unaffected on every round.

#### Scenario: Second FAILED review naming a decision escalates
- **WHEN** a task's round-1 review reports `FAILED` with notes "missing test", the fix runs,
  and the round-2 review reports `FAILED` with `decision_required` set
- **THEN** the task's status is `escalated`, no third fix spawn is dispatched, and the
  round-2 journal entry has `terminal_status: escalated`, `escalation_reason:
  "decision-required"`, a two-round `convergence_summary`, and a `pending_decision` envelope
  whose `question` equals the `decision_required` text and whose `provenance.subject` is
  `<spec_rel>/<task id>`

#### Scenario: First FAILED review naming a decision still gets a fix round
- **WHEN** a task's round-1 review reports `FAILED` with `decision_required` set
- **THEN** the task routes to `fixing` and its journal entry has no `pending_decision` or
  `escalation_reason` key

#### Scenario: Repeated ordinary findings keep the three-round breaker
- **WHEN** a task's rounds 1 and 2 both report `FAILED` with notes that name no decision
- **THEN** the round-2 entry routes to `fixing` with no `escalation_reason`, and the round-3
  `FAILED` review escalates via the existing breaker with no `escalation_reason` key

#### Scenario: Replay of a decision-escalated journal restores escalated
- **WHEN** a run is resumed from a journal whose last entry for a task is a
  decision-required escalation
- **THEN** the task is restored as `escalated`, no review or fix is re-dispatched for it, and
  no second decision record is filed

### Requirement: The decision-required escalation files an idempotent decision record

On a decision-required escalation the orchestrator SHALL call `decisions.ask()` with the
envelope's `decision_id`, `source`, `subject`, `repo`, and `run_id`; the decision text as the
question; a background and context that name the review rounds burned, the acceptance
criterion and the conflicting test/behaviour from the decision text, and the resume recipe
(`worktrail-live clear-task --tasks <id>` on the run, then resume); two options in priority
order — amend the acceptance criterion to admit the existing behaviour, or keep the criterion
literal and change the conflicting test/behaviour — and the reviewer's text as the
recommendation. Filing SHALL be best-effort: any failure (queue root missing, decision
primitives unavailable, an open decision already exists for the identity, or `repo`/`spec_rel`
unavailable to the commit path) SHALL be logged and SHALL NOT change the task's `escalated`
status or the journal entry's `pending_decision` envelope. A second escalation on the same
`(repo, subject, question)` SHALL converge on the existing record rather than filing another.

#### Scenario: Record filed under the work-queue root
- **WHEN** a decision-required escalation occurs with `$WORK_QUEUE_DIR` pointing at a
  writable directory
- **THEN** `decisions/open/` contains one record whose id equals the entry's
  `pending_decision.decision_id`, whose frontmatter carries `source:
  orchestrator-review-loop` and the run's `run-id`, and whose options section lists exactly
  two options

#### Scenario: Filing failure does not touch the run
- **WHEN** a decision-required escalation occurs with `$WORK_QUEUE_DIR` pointing at an
  unwritable or missing path
- **THEN** the task is still `escalated`, the journal entry still carries `pending_decision`,
  and the run continues its other groups

#### Scenario: Same conflict twice files one record
- **WHEN** `ask()` is attempted twice for the same envelope identity
- **THEN** exactly one record exists in `decisions/open/` and the second attempt is logged,
  not raised
