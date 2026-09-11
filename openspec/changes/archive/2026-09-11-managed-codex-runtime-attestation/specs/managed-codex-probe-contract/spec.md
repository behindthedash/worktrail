## MODIFIED Requirements

### Requirement: Every run reports exactly one classified stage outcome
The probe SHALL classify every run's outcome as exactly one of:
`environment_preparation`, `startup`, `provider_selection`, `authentication`,
`timeout`, or `report_back`, together with an actionable, redacted diagnostic
message.  For callers that need managed-runtime attestation, its safe
structured result SHALL additionally expose the selected and documented
effective Codex provider identity and, when selected, model identity; it
shall not treat a session or thread identifier as provider/model identity.

#### Scenario: Successful run reports report_back success
- **WHEN** the nested Codex process starts, reports its effective provider
  identity, demonstrates usable authentication, and returns its no-op reply
  within the timeout
- **THEN** the structured report records a successful `report_back` outcome,
  the selected/effective identities needed for equality checking, and no
  earlier-stage failure

#### Scenario: Effective identity is unavailable
- **WHEN** a caller requests provider/model attestation and the nested Codex
  path cannot supply a documented non-secret effective identity
- **THEN** the structured report identifies `provider_selection` as failed
  and does not substitute a session or thread identifier as identity

#### Scenario: Failure is classified to a single stage
- **WHEN** a probe run fails for any reason
- **THEN** the structured report identifies exactly one of the six stage
  outcomes as the point of failure, with a diagnostic message that does not
  require inspecting raw process output to act on
