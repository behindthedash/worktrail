## ADDED Requirements

### Requirement: A hand-run smoke harness records a real evaluator run
The repo SHALL provide a hand-run harness that builds a fixture intake brief whose focus carries
one refutable claim alongside otherwise-valid directly-actionable work, invokes the real
`evaluate_group()` against a live model, and records the evaluator's untouched `raw_text`
together with the exact focus text the evaluator was shown. The recording SHALL carry the agent
and the capture date, so a stale answer is identifiable. The harness SHALL NOT be reachable from
`pytest` or from any skill's command surface -- running it is always a deliberate operator step.

#### Scenario: a run records the raw evaluator output and its input focus
- **WHEN** an operator runs the harness against this repo
- **THEN** the recorded fixture holds the evaluator's raw output verbatim, the fixture brief's
  focus text, and a metadata block naming the agent and capture date

#### Scenario: the harness leaves no state behind
- **WHEN** the harness builds its fixture brief
- **THEN** it does so under a throwaway queue root, never the operator's real `$WORK_QUEUE_DIR`

### Requirement: The harness adjudicates the recorded run against the Step 2b contract
The harness SHALL check the recorded run against three conditions -- the verdict for the fixture
brief is `work-directly`, it carries a `refuted_span`, and that span is a verbatim substring of
the focus text the evaluator was shown -- and SHALL report which conditions failed, exiting
non-zero when any did. A run whose span is absent or misquoted SHALL NOT be reported as a pass.

#### Scenario: a clean run passes
- **WHEN** the recorded verdict is `work-directly` with a `refuted_span` found verbatim in the
  shown focus
- **THEN** the harness reports a pass and exits zero

#### Scenario: a misquoted span is reported as a miss
- **WHEN** the recorded verdict carries a `refuted_span` that is not a verbatim substring of the
  shown focus
- **THEN** the harness names that failure specifically and exits non-zero

#### Scenario: an omitted span is reported as a miss
- **WHEN** the recorded verdict is `work-directly` with no `refuted_span`
- **THEN** the harness names that failure specifically and exits non-zero

### Requirement: The recorded run is replayed offline through parse and apply
A test SHALL replay the recorded `raw_text` through the production `parse_verdicts()` and
`apply_verdicts()` against a real brief file, asserting that the refuted claim is removed from or
replaced in the brief's focus and that a `## Triage` section recording the correction is
appended. The replay SHALL make no network call and SHALL require no credential, so it runs on
every CI runner.

#### Scenario: a recorded work-directly verdict rewrites the brief
- **WHEN** the recorded raw output is parsed and applied to a brief carrying the recorded focus
- **THEN** the brief's focus no longer contains the refuted span and the file carries a new
  `## Triage` section describing the rewrite

#### Scenario: the replay is offline
- **WHEN** the replay test runs on a machine with no model credential and no network
- **THEN** it passes, because it reads the recorded output rather than calling a model

### Requirement: The replay covers the whole-focus downgrade
The replay SHALL also exercise the whole-focus guard: the recorded verdict, with its
`refuted_span` widened to the brief's entire focus text, SHALL be downgraded to `keep` and SHALL
NOT rewrite the focus.

#### Scenario: a span covering the whole focus downgrades to keep
- **WHEN** the recorded verdict's span is widened to cover the brief's entire focus text
- **THEN** the applied verdict is `keep` and the brief's focus text is unchanged

### Requirement: Step 2b is tightened only on recorded evidence
A change to `EVALUATOR_PROMPT_TEMPLATE`'s Step 2b span rules SHALL be justified by a recorded run
in which the model omitted or misquoted the span, and SHALL be accompanied by a re-recording
showing the tightened prompt fixes it. A run in which the model already quoted the span verbatim
SHALL leave the prompt text unchanged.

#### Scenario: a clean recording leaves the prompt alone
- **WHEN** the recorded run already emits a verbatim `refuted_span`
- **THEN** Step 2b's wording is left as is

#### Scenario: a miss justifies a prompt change
- **WHEN** the recorded run omits or misquotes the span
- **THEN** Step 2b is tightened and a fresh recording is captured showing the corrected behavior
