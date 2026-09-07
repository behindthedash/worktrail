## Purpose

Defines conservative runtime classification and eligibility behavior for dependency references already stored in work-queue briefs, including malformed legacy data.

## ADDED Requirements

### Requirement: Runtime dependency resolution distinguishes reference states
The system SHALL classify each stored `blocked-by` reference as exactly one of `done`, `active`, `stale`, `ambiguous`, or `malformed`, and SHALL retain the raw stored value in the resolution result for diagnostics. Validation of reference syntax SHALL occur independently of whether any queue or picked brief matches the value.

#### Scenario: Queued dependency is active
- **WHEN** a syntactically valid dependency reference uniquely resolves to a brief in the queue
- **THEN** the reference is classified as `active`

#### Scenario: Picked dependency is active
- **WHEN** a syntactically valid dependency reference uniquely resolves to a picked brief whose status is not done
- **THEN** the reference is classified as `active`

#### Scenario: Completed dependency is done
- **WHEN** a syntactically valid dependency reference uniquely resolves to a picked brief whose status is done
- **THEN** the reference is classified as `done`

#### Scenario: Missing valid dependency is stale
- **WHEN** a syntactically valid dependency reference resolves to no queue or picked brief
- **THEN** the reference is classified as `stale`

#### Scenario: Non-unique dependency is ambiguous
- **WHEN** a syntactically valid dependency reference matches multiple briefs in either searched lifecycle location
- **THEN** the reference is classified as `ambiguous`

#### Scenario: Invalid dependency shape is malformed
- **WHEN** a stored dependency value is blank after trimming, contains a comma, or is not a string reference
- **THEN** the reference is classified as `malformed` without reinterpreting or resolving part of its value

### Requirement: Eligibility fails closed except for done and valid stale references
A brief SHALL be eligible with respect to `blocked-by` only when every dependency reference is classified as `done` or `stale`. A dependency classified as `active`, `ambiguous`, or `malformed` SHALL keep the brief blocked for queue eligibility and automatic pickup.

#### Scenario: Valid stale reference remains satisfied
- **WHEN** a brief has a syntactically valid dependency reference absent from both queue and picked storage
- **THEN** the missing reference does not block the brief

#### Scenario: Ambiguous reference remains blocked
- **WHEN** a brief has a dependency reference that resolves ambiguously
- **THEN** the brief is reported blocked and is not eligible for automatic pickup

#### Scenario: Malformed reference remains blocked
- **WHEN** a brief contains a comma-joined or otherwise malformed dependency reference
- **THEN** the brief is reported blocked and is not eligible for automatic pickup

#### Scenario: Comma-joined incident cannot hide an active dependency
- **WHEN** a brief stores several IDs as one comma-joined dependency value and the first embedded ID names an active brief
- **THEN** the whole stored value is classified as `malformed` and the dependent brief remains blocked
- **AND** the system does not split, partially resolve, or treat the value as a stale satisfied reference

### Requirement: Runtime diagnostics identify unresolved dependency values
Runtime consumers that report unresolved `blocked-by` dependencies SHALL derive their result from the structured dependency classification and SHALL identify both the raw dependency value and its `active`, `ambiguous`, or `malformed` state. `done` and valid `stale` references SHALL NOT produce unresolved-dependency diagnostics.

#### Scenario: Malformed claim warning is actionable
- **WHEN** claim-time diagnostics inspect a brief with a malformed stored dependency value
- **THEN** the warning identifies the raw malformed value and labels its resolution state as malformed

#### Scenario: Ambiguous claim warning is distinguishable
- **WHEN** claim-time diagnostics inspect a brief with an ambiguous dependency reference
- **THEN** the warning identifies the raw reference and labels its resolution state as ambiguous

#### Scenario: Satisfied references stay quiet
- **WHEN** claim-time diagnostics inspect dependencies classified as done or stale
- **THEN** no unresolved-dependency warning is emitted for those references

### Requirement: Runtime resolution does not mutate queue briefs
Reading, classifying, checking eligibility, or producing diagnostics for stored dependency references SHALL NOT normalize, split, rewrite, move, or otherwise mutate queue or picked brief files.

#### Scenario: Malformed legacy brief remains byte-for-byte unchanged
- **WHEN** the system evaluates eligibility and diagnostics for an existing brief containing a malformed dependency value
- **THEN** the brief contents and location remain unchanged
