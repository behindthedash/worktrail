## Purpose

Defines how unresolved work-queue dependency-reference classifications are surfaced to queue-listing data, automatic-selection skip reasons, and operator-facing output, so malformed or ambiguous references are diagnosable and provably unable to reach an automatic pick.

## ADDED Requirements

### Requirement: Queue listing data exposes unresolved dependency diagnostics
Queue-listing data SHALL report, for each queued brief, a diagnostics collection containing one entry per `blocked-by` reference that is not satisfied. Each entry SHALL carry the raw stored value, the normalized reference when shape checking succeeded, the resolution state, and the matching candidates the reference resolved to. References classified as done or as valid stale SHALL contribute no entry. Every field previously present in queue-listing data SHALL remain present and unchanged in meaning.

#### Scenario: Malformed reference appears in listing diagnostics
- **WHEN** a queued brief stores a comma-joined `blocked-by` value and queue-listing data is produced
- **THEN** that brief's diagnostics collection contains one entry whose state is `malformed` and whose raw value is the stored value exactly as written

#### Scenario: Ambiguous reference appears in listing diagnostics
- **WHEN** a queued brief stores a `blocked-by` reference that matches more than one brief
- **THEN** that brief's diagnostics collection contains one entry whose state is `ambiguous` and which lists the matching candidates

#### Scenario: Active reference appears in listing diagnostics
- **WHEN** a queued brief stores a `blocked-by` reference that uniquely names a brief that is not done
- **THEN** that brief's diagnostics collection contains one entry whose state is `active`

#### Scenario: Satisfied references contribute no diagnostics
- **WHEN** a queued brief's `blocked-by` references are all classified as done or as valid stale
- **THEN** that brief's diagnostics collection is empty and the brief is not reported blocked on dependency grounds

#### Scenario: Existing listing fields are preserved
- **WHEN** queue-listing data is produced for briefs with and without dependency problems
- **THEN** the previously defined per-brief fields, including the blocked flag, are still emitted with their existing meanings

### Requirement: Automatic selection reports a dependency-specific skip reason
When automatic selection skips a brief because a `blocked-by` reference is malformed or ambiguous, it SHALL record a stable structured skip reason that identifies that dependency state, distinct from the reason recorded for ordinary blocking. The recorded reason SHALL retain `blocked` as its leading coarse category so existing miss-log aggregation continues to bucket it as blocked. A brief blocked only by an active dependency or an open decision SHALL continue to record the existing unqualified blocked reason.

#### Scenario: Malformed dependency yields a malformed skip reason
- **WHEN** automatic selection evaluates a queued brief whose `blocked-by` value is malformed
- **THEN** the brief is skipped and its recorded reason identifies a malformed dependency reference

#### Scenario: Ambiguous dependency yields an ambiguous skip reason
- **WHEN** automatic selection evaluates a queued brief whose `blocked-by` reference is ambiguous
- **THEN** the brief is skipped and its recorded reason identifies an ambiguous dependency reference

#### Scenario: Ordinary blocking keeps the existing reason
- **WHEN** automatic selection evaluates a queued brief blocked only by an active dependency or an open decision
- **THEN** the brief is skipped with the existing unqualified blocked reason

#### Scenario: Miss-log aggregation still counts these as blocked
- **WHEN** automatic selection finds nothing to pick and records why
- **THEN** a brief skipped for a malformed or ambiguous dependency is counted under the same coarse blocked category as other blocked briefs, while the per-brief entry retains the dependency-specific reason

### Requirement: Operator output names the affected brief and the invalid value
Human-readable queue listing and the rendered dashboard SHALL warn about queued briefs holding a malformed or ambiguous `blocked-by` reference. The warning SHALL identify the affected brief and, for a malformed reference, the raw stored value, so an operator can locate and repair the brief without inspecting stored files or reading logs. Briefs blocked only by satisfied-or-active references SHALL NOT trigger this warning.

#### Scenario: Human queue listing warns about a malformed reference
- **WHEN** an operator lists the queue in human-readable form and a queued brief holds a comma-joined `blocked-by` value
- **THEN** the output contains a warning naming that brief and showing the raw stored value

#### Scenario: Rendered dashboard flags the dependency problem
- **WHEN** the dashboard renders a queue containing a brief with a malformed or ambiguous dependency reference
- **THEN** that brief is presented as blocked by a dependency-reference problem rather than as ordinarily blocked

#### Scenario: Ordinary blocked briefs produce no repair warning
- **WHEN** an operator lists the queue and the only blocked brief is waiting on an active prerequisite
- **THEN** no malformed or ambiguous dependency warning is shown

### Requirement: Malformed dependency references never reach an automatic pick
A brief whose `blocked-by` holds a malformed reference SHALL NOT be returned as the automatic pick, including when an embedded fragment of that value names a brief that is still active. Automatic selection SHALL continue to return an otherwise eligible brief from the same queue.

#### Scenario: Comma-joined incident is skipped end to end
- **WHEN** a queue contains a brief whose single `blocked-by` item joins several identifiers with commas, the first of those identifiers names a brief still queued, and automatic selection runs over the listing data produced from that queue
- **THEN** the malformed brief is not the pick and is recorded as skipped with a malformed-dependency reason

#### Scenario: A clean brief in the same queue is still pickable
- **WHEN** the same queue also contains an eligible brief with no dependency problems
- **THEN** automatic selection returns that brief as the pick

### Requirement: Diagnostics are produced without modifying stored briefs
Producing queue-listing diagnostics, automatic-selection skip reasons, or operator warnings SHALL NOT normalize, split, repair, move, or otherwise modify any queue or picked brief, and SHALL NOT alter which values are accepted when a brief is created.

#### Scenario: Diagnosed brief is left byte-for-byte unchanged
- **WHEN** listing data, automatic selection, and operator output are all produced for a queue containing a malformed brief
- **THEN** that brief's contents and location are unchanged afterward
