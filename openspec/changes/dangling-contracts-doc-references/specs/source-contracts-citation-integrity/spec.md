## ADDED Requirements

### Requirement: Root-relative contracts doc citations resolve
Python sources under `src/` and `tests/` SHALL NOT cite a root-relative
`contracts/<name>.md` path that does not exist at the repository root. A citation is a
`contracts/` path segment at a token start (not preceded by a path character) followed by
a Markdown filename, including one whose filename continues on the next source line across
a comment continuation. A path carrying any prefix (`docs/specs/<id>/contracts/x.md`,
`../contracts/x.md`), a glob (`contracts/*.md`), a bare directory mention (`contracts/`
with no Markdown filename), or a non-Markdown target (`.fixtures/contracts/*.json`) is not
a citation of this class and SHALL NOT be flagged. `tests/test_no_dangling_contracts_doc_refs.py`
SHALL implement this rule as a structural guard over the scanned trees and SHALL fail,
naming the citing file and line, when a citation's path does not exist.

#### Scenario: A dangling citation fails the suite
- **WHEN** a Python source under `src/` or `tests/` cites `contracts/example.md` and no
  `contracts/example.md` exists at the repository root
- **THEN** the guard fails and its report names the citing file and line

#### Scenario: A citation split across source lines is seen
- **WHEN** a comment cites `contracts/example-` and its continuation line carries the rest
  of the filename, as the two wrapped sites in this repository did
- **THEN** the guard reconstructs the filename and applies the same resolution rule

#### Scenario: Spec-folder, glob, and fixture shapes are not citations
- **WHEN** a source contains `docs/specs/<id>/contracts/api.md`, `../contracts/note.md`,
  the glob `contracts/*.md`, a bare `contracts/` directory mention, or a
  `.fixtures/contracts/*.json` path
- **THEN** the guard flags none of them

#### Scenario: The guard cannot pass by scanning nothing
- **WHEN** the guard runs over the repository
- **THEN** it has walked both `src/` and `tests/`, failing loudly if either tree is
  missing or the walk yields no files, and reports no citation

### Requirement: The active-conflicts description matches its command
The `worktrail-run-record` module docstring's `active-conflicts` entry SHALL describe the
`{"live": [...], "stale": [...]}` partition that `cmd_active_conflicts` prints, and SHALL
NOT cite a `contracts/` document.

#### Scenario: The entry matches the printed shape
- **WHEN** a reader consults the module docstring's `active-conflicts` entry
- **THEN** it names the live/stale partition, matching the command's own docstring and
  output, and cites no `contracts/` path
