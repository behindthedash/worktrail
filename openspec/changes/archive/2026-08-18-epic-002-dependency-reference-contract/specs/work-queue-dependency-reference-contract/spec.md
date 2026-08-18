## Purpose

Defines safe producer-side dependency-reference input so supported handoff creation cannot persist ambiguous prerequisite metadata in the work queue.

## ADDED Requirements

### Requirement: A blocked-by value represents exactly one dependency reference
Each `blocked-by` value accepted by the supported handoff creation API or CLI SHALL be a non-empty string after surrounding whitespace is removed and SHALL NOT contain a comma. The contract SHALL continue to accept otherwise valid brief identifiers understood by dependency resolution, including full brief IDs and prefixes, without requiring that the referenced brief currently exists.

#### Scenario: Full dependency ID is accepted
- **WHEN** a caller creates a handoff with one non-empty full brief ID as a dependency
- **THEN** the handoff is created with that ID as one `blocked-by` list entry

#### Scenario: Dependency prefix is accepted
- **WHEN** a caller creates a handoff with a non-empty brief ID prefix that contains no comma
- **THEN** the handoff is created without requiring the prefix to resolve at creation time

#### Scenario: Blank dependency value is rejected
- **WHEN** a caller supplies an empty or whitespace-only dependency value
- **THEN** handoff creation fails before a brief is written and identifies that each `blocked-by` value must be non-empty

#### Scenario: Comma-joined dependency value is rejected
- **WHEN** a caller supplies two or more dependency references joined into one comma-containing value
- **THEN** handoff creation fails before a brief is written and identifies the offending value

### Requirement: CLI callers receive actionable repeated-flag guidance
When the CLI rejects a comma-containing `--blocked-by` value, it SHALL return a non-zero status and SHALL tell the caller to pass one dependency per repeated `--blocked-by` flag.

#### Scenario: CLI explains how to provide multiple dependencies
- **WHEN** a caller invokes the CLI with `--blocked-by dep-a,dep-b`
- **THEN** stderr includes an actionable example or instruction using `--blocked-by dep-a --blocked-by dep-b`
- **AND** no queue brief is created

### Requirement: Repeated blocked-by arguments preserve dependency list structure
The handoff CLI SHALL preserve valid repeated `--blocked-by` arguments as separate ordered entries in the created brief's `blocked-by` list, and the Python API SHALL apply the same per-value validation contract to iterable input.

#### Scenario: Repeated CLI flags become separate entries
- **WHEN** a caller invokes the CLI with `--blocked-by dep-a --blocked-by dep-b`
- **THEN** the created brief contains `blocked-by` as the ordered list `[dep-a, dep-b]`

#### Scenario: Python API rejects the same malformed shape
- **WHEN** a non-CLI caller passes an iterable containing `dep-a,dep-b` as one `blocked_by` value
- **THEN** the API raises a validation error before creating queue storage or a brief

### Requirement: Producer validation does not reinterpret existing queue data
This capability SHALL validate only values entering through supported handoff creation and SHALL NOT normalize, split, rewrite, or define runtime eligibility behavior for malformed dependency references already present in queue files.

#### Scenario: Existing malformed brief remains untouched
- **WHEN** handoff creation validates a new request while another queue brief already contains malformed dependency metadata
- **THEN** the existing brief is neither rewritten nor normalized by this capability

