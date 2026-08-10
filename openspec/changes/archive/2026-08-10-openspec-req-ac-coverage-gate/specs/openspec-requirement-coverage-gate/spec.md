## Purpose

Guarantees that every requirement an OpenSpec change declares in its
`specs/**/spec.md` delta files is referenced by that change's `tasks.md`, so
a change cannot silently add a requirement no task ever addresses.

## ADDED Requirements

### Requirement: Declared Requirement Discovery

The system SHALL discover the requirement names an OpenSpec change declares by
reading the `### Requirement: <Name>` headers under every `## ADDED
Requirements` and `## MODIFIED Requirements` section across that change's
`specs/**/spec.md` delta files.

#### Scenario: Requirement declared under ADDED Requirements

- **WHEN** a change's delta spec declares `### Requirement: Foo` under `##
  ADDED Requirements`
- **THEN** `Foo` SHALL be included in the change's declared requirement set

#### Scenario: Requirement declared under MODIFIED Requirements

- **WHEN** a change's delta spec declares `### Requirement: Foo` under `##
  MODIFIED Requirements`
- **THEN** `Foo` SHALL be included in the change's declared requirement set

#### Scenario: Requirement name appears only under REMOVED Requirements

- **WHEN** a change's delta spec declares `### Requirement: Foo` only under a
  `## REMOVED Requirements` section
- **THEN** `Foo` SHALL NOT be included in the change's declared requirement
  set, since a removal claims no new task coverage

### Requirement: Task Reference Matching

The system SHALL consider a declared requirement covered when its name is
present, case-insensitively, anywhere in the change's `tasks.md` text. This
is a name-presence heuristic, not a structured field lookup: OpenSpec's
`tasks.md` carries no per-task requirement-reference field equivalent to
devkit's `reqs`/`ac-mapping` frontmatter arrays.

#### Scenario: Requirement name appears in a task description

- **WHEN** `tasks.md` contains a task line whose text includes the declared
  requirement's name
- **THEN** that requirement SHALL be considered covered

#### Scenario: Requirement name does not appear anywhere in tasks.md

- **WHEN** a declared requirement's name appears nowhere in `tasks.md`'s text
- **THEN** that requirement SHALL be considered uncovered

#### Scenario: Change has no tasks.md

- **WHEN** a change declares requirements but has no `tasks.md` file
- **THEN** every declared requirement SHALL be considered uncovered

### Requirement: Uncovered Requirement Detection

The system SHALL report every requirement declared by a change's delta specs
that is uncovered per the Task Reference Matching requirement, naming the
change and the specific uncovered requirement names.

#### Scenario: One declared requirement has no task reference

- **WHEN** a change declares three requirements and `tasks.md`'s text
  references only two of them by name
- **THEN** the system SHALL report the third requirement as uncovered

#### Scenario: Every declared requirement is referenced

- **WHEN** every requirement a change declares is referenced by name in
  `tasks.md`
- **THEN** the system SHALL report no uncovered requirements for that change

### Requirement: Non-Retroactive Gate Enforcement

When enforcing at `worktrail-compile`'s step-3 scope-check, the system SHALL
fail only for requirement names newly declared by the change being compiled
relative to the requirement names already present in `openspec/specs/` for
the same capability path. A requirement that was already declared and already
uncovered before this change SHALL NOT fail the gate.

#### Scenario: Change adds a new capability with an uncovered requirement

- **WHEN** a change proposes a brand-new capability (no existing
  `openspec/specs/<path>/spec.md`) and declares a requirement with no
  `tasks.md` reference
- **THEN** the gate SHALL fail and name that requirement, since every
  requirement of a brand-new capability is newly declared

#### Scenario: Change modifies a capability without touching a pre-existing gap

- **WHEN** a change's `## MODIFIED Requirements` section edits a requirement
  that already existed in `openspec/specs/<path>/spec.md`, and the change
  introduces no requirement name absent from that existing spec
- **THEN** the gate SHALL NOT fail on that requirement, regardless of whether
  it is referenced in `tasks.md`

#### Scenario: Change adds a requirement together with its task coverage

- **WHEN** a change declares a new requirement `Foo` and `tasks.md` contains
  a task line referencing `Foo` by name
- **THEN** the gate SHALL pass for `Foo`

### Requirement: Compile-Step Gate Integration

`worktrail-compile`'s step-3 scope-check SHALL run the requirement-coverage
check for every OpenSpec-format change it compiles and SHALL fail the compile
— before the change's docs-only spec PR is pushed — when a newly-declared
requirement is uncovered per the Non-Retroactive Gate Enforcement requirement.

#### Scenario: Coverage failure blocks the spec PR

- **WHEN** `worktrail-compile` runs against an OpenSpec change with a
  newly-declared, uncovered requirement
- **THEN** compile SHALL exit non-zero, naming the uncovered requirement, and
  the calling pipeline SHALL NOT push the spec PR on that failing compile

#### Scenario: Coverage passes

- **WHEN** `worktrail-compile` runs against an OpenSpec change with no
  newly-declared uncovered requirements
- **THEN** the requirement-coverage check SHALL NOT affect compile's existing
  file-scope/dependency-ordering result
