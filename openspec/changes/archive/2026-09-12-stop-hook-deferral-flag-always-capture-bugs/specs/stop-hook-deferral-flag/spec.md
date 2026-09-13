## MODIFIED Requirements

### Requirement: Additive And Non-Interfering
The system SHALL implement the deferral flag as a check separate from the Stop hook's base
instruction.

The base instruction SHALL require the agent to use `worktrail-handoff` to capture one brief for
each distinct verified defect, bug, or regression that was discovered during the session and left
unfixed. This capture SHALL be independent of the EXCEPTIONAL-VALUE gate. A defect counts as
verified when it was reproduced or directly evidenced; a hypothesis does not count.

The EXCEPTIONAL-VALUE gate SHALL govern only forward-looking next-step ideas and SHALL NOT
suppress defect capture. A defect within the current request SHALL NOT be captured as a handoff.
It SHALL remain under the base instruction's completion audit: the agent fixes it in the session
or stops on a verified blocker.

The deferral flag SHALL NOT alter the base instruction's text or the conditions under which it
fires.

#### Scenario: Unfixed verified defect is captured regardless of the gate
- **WHEN** a session ends with substantive work and the Stop hook fires
- **THEN** the printed instruction requires one `worktrail-handoff` capture per distinct verified
  defect discovered but not fixed in the session
- **AND** it states that the EXCEPTIONAL-VALUE gate does not apply to those captures

#### Scenario: EXCEPTIONAL-VALUE gate governs only forward-looking ideas
- **WHEN** the Stop hook fires
- **THEN** the printed instruction applies the EXCEPTIONAL-VALUE gate and its routine-polish,
  cleanup, and refactor exclusions only to forward-looking ideas
- **AND** it permits the reply "No handoff captured; no exceptional next step identified." only
  when no unfixed verified defect was left uncaptured

#### Scenario: In-scope defect is fixed, not handed off
- **WHEN** the Stop hook fires
- **THEN** the printed instruction directs that a defect within the current request be fixed now
  or stopped on a verified blocker, not captured as a handoff

#### Scenario: EXCEPTIONAL-VALUE gate output is unchanged
- **WHEN** a session ends with substantive work and no unmatched `deferred_work` entry
- **THEN** the hook's printed instruction is byte-for-byte identical to the base instruction
  defined by this requirement, with no deferral-flag block

#### Scenario: Both checks can fire in the same session
- **WHEN** a session ends with substantive work and an unmatched `deferred_work` entry
- **THEN** the hook's output includes the unmodified base instruction plus an additional,
  separate deferral-flag block
