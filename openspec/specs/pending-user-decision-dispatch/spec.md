# pending-user-decision-dispatch Specification

## Purpose
Defines how guard decisions cross Worktrail's front-door and execution boundaries without depending on a particular agent provider's prompt API.
## Requirements
### Requirement: Guards produce a stable decision envelope
When a collision, staleness, or related-brief guard requires operator input, Worktrail SHALL produce a structured pending decision containing a stable decision ID, guard kind, question, ordered typed options, source run and brief identities when present, and opaque resume data. Re-evaluating the same unresolved guard for the same source SHALL preserve its decision identity and SHALL NOT create duplicate open decisions.

#### Scenario: Guard pauses before mutation
- **WHEN** a guard cannot safely choose an outcome without operator input
- **THEN** Worktrail returns and records the pending decision before dispatching implementation or mutating the guarded brief state

#### Scenario: Re-entry preserves identity
- **WHEN** the same run resumes while its guard decision remains unanswered
- **THEN** Worktrail surfaces the existing decision ID and options rather than filing a second decision

### Requirement: Attended hosts present and resume the same contract
An attended Worktrail invocation SHALL map the decision envelope to the prompt facility supported by its Claude, Codex, or OpenCode host. The selected option or validated free-form response SHALL be persisted against the decision ID before execution resumes, and the resumed guard SHALL consume that recorded answer rather than infer a provider-specific result.

#### Scenario: Native Claude prompt
- **WHEN** an attended Claude session receives a pending decision and native `AskUserQuestion` is available
- **THEN** it presents the envelope through that facility and resumes with the persisted answer

#### Scenario: Interactive Codex or OpenCode prompt
- **WHEN** an attended Codex or OpenCode session receives a pending decision
- **THEN** the front door uses the host-supported prompt/resume mechanism while preserving the same decision ID, options, and recorded answer

### Requirement: Unattended execution fails closed with a recoverable result
Headless skill-adapter, subprocess, and drain execution SHALL NOT choose a guard option. It SHALL terminate or yield ownership with a machine-readable `pending_user_decision` result containing the decision ID and resume context, while leaving the linked brief claim recoverable and discoverable.

#### Scenario: Headless adapter reaches a guard
- **WHEN** a headless adapter or subprocess reaches an unanswered guard decision
- **THEN** it records `pending_user_decision`, does not launch implementation, and exposes enough identity for a later attended invocation to answer and resume

#### Scenario: Drain observes pending decision
- **WHEN** a drain iteration receives a `pending_user_decision` result
- **THEN** it treats that result as a deliberate non-success terminal handoff, does not guess or spin on the same brief, and leaves the decision visible to the operator

### Requirement: Resume validates decision provenance and freshness
Before applying a recorded answer, Worktrail SHALL verify that the decision belongs to the current run or claimed brief and to the expected guard kind, and SHALL re-run the guard's read-only evidence check. If the guarded facts changed so that the original options are no longer valid, Worktrail SHALL supersede the stale decision with an auditable replacement rather than applying its answer.

#### Scenario: Valid answer resumes exactly once
- **WHEN** a matching open decision has a valid answer and the guarded evidence is unchanged
- **THEN** Worktrail records consumption and resumes the guarded continuation exactly once

#### Scenario: Evidence changed after answer
- **WHEN** the source state changes before a recorded answer is consumed
- **THEN** Worktrail does not apply the stale answer and records a replacement or resolved-no-longer-applicable outcome with lineage to the original decision

### Requirement: Decision lifecycle is auditable across dispatch modes
Run records and the durable human decision queue SHALL retain filing, presentation, answer, consumption, supersession, and resume outcomes with the decision ID and dispatch mode. Native Skill, adapter, subprocess, and drain paths SHALL expose equivalent lifecycle semantics.

#### Scenario: Provider matrix produces equivalent audit state
- **WHEN** equivalent guard cases are exercised through each supported provider and dispatch mode
- **THEN** their durable records contain the same decision lifecycle states and differ only in host presentation metadata

