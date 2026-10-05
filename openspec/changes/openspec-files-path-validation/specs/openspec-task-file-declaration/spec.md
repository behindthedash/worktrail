## ADDED Requirements

### Requirement: Inline file-scope tokens are path-like repository paths

The OpenSpec checklist parser SHALL accept an inline `files:` token as declared file scope only
when it is a path-like, repository-relative path: it MUST name a file or dotfile, or include a
non-empty directory component; it MUST NOT be absolute or contain a parent-directory traversal.
The parser SHALL preserve the existing support for comma- and whitespace-separated tokens and
backtick-wrapped paths. It SHALL not turn prose following `files:` into declared file scope.

#### Scenario: Valid declared paths are retained

- **WHEN** a task declares source, test, root-file, or dotfile paths using the supported
  separators or backticks
- **THEN** those valid repository-relative paths appear in the task's declared file scope in
  their authored order

#### Scenario: Prose does not create a declared scope

- **WHEN** an author writes prose such as `files: update the parser tests` rather than path
  tokens
- **THEN** none of the prose words appear in the task's declared file scope

#### Scenario: Unsafe paths do not create a declared scope

- **WHEN** a `files:` declaration includes an absolute path or a path with a parent-directory
  traversal
- **THEN** that token does not appear in the task's declared file scope

### Requirement: Invalid inline file-scope tokens warn tolerantly

For every invalid token in an inline `files:` declaration, the parser SHALL record a warning that
identifies the task and the rejected token, then continue parsing the remaining declaration and
tasks. A declaration from which all tokens are rejected SHALL behave as undeclared scope and
SHALL retain the existing empty-declaration warning behavior rather than causing a hard parse
failure.

#### Scenario: Mixed declaration keeps only valid paths

- **WHEN** a task's `files:` line contains valid paths together with prose or unsafe tokens
- **THEN** the task retains only the valid paths and records a warning for each rejected token

#### Scenario: Entirely invalid declaration remains non-fatal

- **WHEN** every token on a task's `files:` line is invalid
- **THEN** parsing succeeds, the task has no declared file scope, and warnings identify the
  rejected tokens and the empty resulting declaration
