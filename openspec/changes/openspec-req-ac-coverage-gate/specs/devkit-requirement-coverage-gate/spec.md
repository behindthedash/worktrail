## MODIFIED Requirements

### Requirement: Format Scoping

The coverage check SHALL apply only to devkit-format specs and SHALL leave the
OpenSpec-format path unchanged. The OpenSpec-format path's own requirement
coverage is enforced independently by the `openspec-requirement-coverage-gate`
capability, at `worktrail-compile`'s step-3 scope-check, not by this
devkit-scoped check and not by `worktrail-compile`'s pre-existing file-scope
inference.

#### Scenario: Repository contains an OpenSpec change

- **WHEN** a diff modifies an OpenSpec-format change directory
- **THEN** the devkit coverage check SHALL report nothing for it, and any
  requirement-coverage enforcement for that change is owned by the
  `openspec-requirement-coverage-gate` capability, not by this check

#### Scenario: Repository has no devkit spec tree

- **WHEN** a repository contains no devkit-format spec directory
- **THEN** the check SHALL pass without error
