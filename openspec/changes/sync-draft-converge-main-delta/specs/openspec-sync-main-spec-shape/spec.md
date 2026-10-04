## ADDED Requirements

### Requirement: A new main spec converges on the repository's existing shape

When the bundled sync operation creates a canonical `openspec/specs/<capability>/spec.md` that
does not yet exist, it SHALL match the heading shape of the repository's other canonical
capability specs rather than stamping a fixed title line. If the repository's existing canonical
specs carry no top-level `# <capability> Specification` title line, the new spec SHALL begin with
its `## Purpose` heading and SHALL NOT gain a title line. A title line SHALL be written only when
the repository's existing canonical specs already carry one, or when the repository has no
canonical capability spec to converge on. The step SHALL NOT cite `openspec validate` as
requiring a title line: a main spec with no title line validates clean.

#### Scenario: a repository whose canonical specs have no title line

- **WHEN** every existing `openspec/specs/*/spec.md` in the target repository begins with `## Purpose` and the sync creates a new capability spec
- **THEN** the new spec begins with `## Purpose` and carries no `# ... Specification` title line

#### Scenario: a repository whose canonical specs carry a title line

- **WHEN** the target repository's existing canonical specs begin with a `# <capability> Specification` title line and the sync creates a new capability spec
- **THEN** the new spec begins with that title line

#### Scenario: no canonical spec exists to converge on

- **WHEN** the target repository has no existing canonical capability spec
- **THEN** the sync writes the title line and the new spec validates

### Requirement: The sync never adds a title line to an existing main spec

When the bundled sync operation applies an `ADDED`, `MODIFIED`, or `RENAMED` requirement to a
canonical `openspec/specs/<capability>/spec.md` that already exists, it SHALL edit only the
requirement and scenario content the delta names. It SHALL NOT add, remove, reword, or reposition
a top-level `# ... Specification` title line, and SHALL NOT restyle any other heading the delta
does not name.

#### Scenario: existing spec gains delta content but keeps its heading shape

- **WHEN** a delta adds scenarios to an existing canonical spec whose first line is `## Purpose`
- **THEN** the synced spec still begins with `## Purpose`, gains only the named requirement and scenario content, and no title line is inserted above it

#### Scenario: the no-restyle rule is present in the bundled skill

- **WHEN** the plugin surface test reads `skills/openspec-sync-specs/SKILL.md`
- **THEN** it finds the rule that an existing main spec's title line is never added, reworded, or moved
