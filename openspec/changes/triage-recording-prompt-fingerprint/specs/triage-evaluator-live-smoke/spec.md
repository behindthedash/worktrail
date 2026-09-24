## MODIFIED Requirements

### Requirement: A hand-run smoke harness records a real evaluator run
The repo SHALL provide a hand-run harness that builds a fixture intake brief whose focus carries
one refutable claim alongside otherwise-valid directly-actionable work, invokes the real
`evaluate_group()` against a live model, and records the evaluator's untouched `raw_text`
together with the exact focus text the evaluator was shown. The recording SHALL carry the agent,
capture date, and a deterministic fingerprint of the evaluator prompt template, so both a stale
answer and the prompt revision that produced it are identifiable. The harness SHALL NOT be
reachable from `pytest` or from any skill's command surface -- running it is always a deliberate
operator step.

#### Scenario: a run records the raw evaluator output and its input focus
- **WHEN** an operator runs the harness against this repo
- **THEN** the recorded fixture holds the evaluator's raw output verbatim, the fixture brief's
  focus text, and a metadata block naming the agent, capture date, and prompt-template
  fingerprint

#### Scenario: the harness leaves no state behind
- **WHEN** the harness builds its fixture brief
- **THEN** it does so under a throwaway queue root, never the operator's real `$WORK_QUEUE_DIR`

## ADDED Requirements

### Requirement: Offline replay verifies prompt provenance
The offline replay SHALL require a prompt-template fingerprint in the committed recording and
shall compare it to the fingerprint of the current evaluator prompt template. It SHALL fail when
the field is missing or does not match, so a prompt-template edit requires an intentional fresh
recording or fixture-provenance update. The comparison SHALL remain offline and SHALL NOT invoke
an evaluator, network service, or credential.

#### Scenario: recorded prompt matches the current template
- **WHEN** the fixture's prompt-template fingerprint equals the current evaluator prompt
  template's fingerprint
- **THEN** the provenance check passes while the recorded answer continues to replay offline

#### Scenario: prompt-template provenance is absent or stale
- **WHEN** the fixture omits its prompt-template fingerprint or its value differs from the
  current evaluator prompt template's fingerprint
- **THEN** the replay fails and identifies the missing or mismatched provenance
