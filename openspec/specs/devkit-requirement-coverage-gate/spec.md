# devkit-requirement-coverage-gate Specification

## Purpose
Guarantees that every requirement and acceptance criterion a devkit-format spec
declares is claimed by at least one of that spec's task files, so a spec
amendment cannot silently add requirements that no task will ever implement.
## Requirements
### Requirement: Declared Identifier Discovery

The system SHALL discover the requirement and acceptance-criterion identifiers a
devkit-format spec declares by reading that spec's own main document, and SHALL
NOT rely on a fixed set of identifier prefixes. Identifier discovery SHALL
recognise both plain sequence identifiers (for example `REQ-001`, `AC-014`,
`FR-023`) and sub-namespaced variants (for example `REQ-NR003`).

#### Scenario: Spec declares identifiers under a non-REQ prefix

- **WHEN** a devkit spec's main document declares identifiers using a prefix
  other than `REQ` or `AC` (for example `FR-001`, `AUD-004`, `AUTHZ-002`)
- **THEN** those identifiers SHALL be included in the spec's declared set

#### Scenario: Spec declares negative-requirement variants

- **WHEN** a devkit spec's main document declares an identifier of the form
  `REQ-NR001`
- **THEN** that identifier SHALL be treated as a distinct declared identifier,
  separate from `REQ-001`

#### Scenario: Identifier appears only in prose, not as a declaration

- **WHEN** an identifier string appears in the main document solely as a
  cross-reference inside descriptive prose, and is never declared as a
  requirement or acceptance criterion in its own right
- **THEN** it SHALL NOT be reported as an uncovered identifier on the basis of
  that prose mention alone

### Requirement: Task Reference Collection

The system SHALL collect the set of identifiers referenced by a spec's tasks
from the union of the `reqs`, `ac-mapping`, and `imp-requirements` frontmatter
arrays across every task file belonging to that spec. A reference in any one of
those arrays SHALL count as coverage.

#### Scenario: Identifier referenced through ac-mapping only

- **WHEN** a declared identifier appears in some task's `ac-mapping` array but
  in no task's `reqs` array
- **THEN** that identifier SHALL be considered covered

#### Scenario: Spec has no task files

- **WHEN** a devkit spec declares identifiers but contains no task files
- **THEN** every declared identifier SHALL be reported as uncovered

### Requirement: Uncovered Identifier Detection

The system SHALL report every identifier that is declared by a spec's main
document and referenced by none of that spec's task files. Each report SHALL
name the spec and the specific uncovered identifiers, so an operator can act on
it without re-deriving the comparison by hand.

#### Scenario: Declared identifier has zero task references

- **WHEN** a spec declares `REQ-023` through `REQ-028` and no task file
  references any of them
- **THEN** the system SHALL report those six identifiers as uncovered for that
  spec

#### Scenario: Every declared identifier is referenced

- **WHEN** every identifier a spec declares is referenced by at least one task
  file
- **THEN** the system SHALL report no uncovered identifiers for that spec

### Requirement: Non-Retroactive Gate Enforcement

When enforcing at the pre-PR gate, the system SHALL fail only for identifiers
that are newly declared by the current diff relative to the base ref. An
identifier that was already declared and already uncovered on the base ref SHALL
NOT fail the gate. The gate SHALL NOT require any baseline file, allowlist, or
per-spec opt-out marker to achieve this.

#### Scenario: Diff adds an uncovered requirement

- **WHEN** a diff adds identifier `REQ-031` to a spec's main document and adds
  no task reference to it
- **THEN** the gate SHALL fail and name `REQ-031`

#### Scenario: Diff touches a spec carrying a pre-existing gap

- **WHEN** a diff edits a spec that already contained uncovered identifiers on
  the base ref, and introduces no newly-declared uncovered identifier
- **THEN** the gate SHALL pass

#### Scenario: Diff adds a requirement together with its task coverage

- **WHEN** a diff adds identifier `REQ-031` to a spec's main document and, in
  the same diff, adds a task file referencing `REQ-031`
- **THEN** the gate SHALL pass

#### Scenario: Base ref cannot be resolved

- **WHEN** the base ref needed for the newly-declared comparison cannot be
  resolved
- **THEN** the gate SHALL NOT fail the run on that basis, and SHALL report that
  the coverage comparison was skipped

### Requirement: Pre-PR Gate Integration

The pre-PR gate SHALL run the coverage check as part of its existing check
sequence and SHALL surface a failure through a dedicated exit code distinct from
those already assigned to the unconfigured, spec-sync, scope-completeness,
clarification-integrity, and DoD-verification outcomes. On failure the gate SHALL
NOT proceed to open a pull request.

#### Scenario: Coverage failure blocks the pull request

- **WHEN** the coverage check reports a newly-declared uncovered identifier
- **THEN** the gate SHALL exit with the dedicated coverage exit code and no pull
  request SHALL be opened

#### Scenario: Coverage passes

- **WHEN** the coverage check reports no newly-declared uncovered identifiers
- **THEN** the gate SHALL continue to its remaining checks unchanged

### Requirement: Repo-Wide Audit Mode

The system SHALL provide an opt-in mode that enumerates uncovered identifiers
across an entire devkit spec corpus, independent of any diff. This mode SHALL be
available for deliberate cleanup work and SHALL NOT be part of the blocking
pre-PR gate.

#### Scenario: Operator audits a whole corpus

- **WHEN** an operator invokes the audit mode against a repository
- **THEN** the system SHALL report uncovered identifiers for every devkit spec
  in that repository, including gaps that predate the current diff

#### Scenario: Audit mode does not gate

- **WHEN** the audit mode reports uncovered identifiers
- **THEN** that result SHALL NOT by itself cause the pre-PR gate to fail

### Requirement: Format Scoping

The coverage check SHALL apply only to devkit-format specs and SHALL leave the
OpenSpec-format path unchanged, where equivalent coverage is already enforced by
the existing scope-check.

#### Scenario: Repository contains an OpenSpec change

- **WHEN** a diff modifies an OpenSpec-format change directory
- **THEN** the devkit coverage check SHALL report nothing for it and the
  existing OpenSpec scope-check behavior SHALL be unaffected

#### Scenario: Repository has no devkit spec tree

- **WHEN** a repository contains no devkit-format spec directory
- **THEN** the check SHALL pass without error

