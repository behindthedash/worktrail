## Purpose

Ensure the required CI check rejects repository Python that the configured interpreter cannot
parse, before tests or packaging can conceal a syntax defect.

## ADDED Requirements

### Requirement: CI parses every tracked Python file
For every non-bookkeeping run of the required `Lint, Test & Build` job, CI SHALL parse every
tracked `*.py` file in the repository using that matrix job's configured Python interpreter. The
parse check SHALL run before pytest and SHALL fail the job when any such file cannot be parsed.

#### Scenario: Repository Python is valid
- **WHEN** every tracked Python file can be parsed by the configured interpreter
- **THEN** the parse check exits successfully and the job proceeds to pytest

#### Scenario: A tracked source file is invalid
- **WHEN** a tracked `src/worktrail/` Python file contains syntax the configured interpreter
  rejects
- **THEN** the parse check fails the `Lint, Test & Build` job before pytest runs

#### Scenario: Bookkeeping-only change bypasses executable CI steps
- **WHEN** the existing changes job classifies a pull request as bookkeeping-only
- **THEN** the parse check has the same per-step condition as the other executable steps and does
  not run, while the required job itself still posts its check run

### Requirement: Parse failures identify every invalid file
The parse check SHALL inspect every selected file rather than stop at the first error. For each
failure it SHALL write the repository-relative path and the interpreter's syntax diagnostic to
stderr, and it SHALL exit non-zero after reporting all failures. A successful check SHALL NOT
create or modify bytecode or other repository files.

#### Scenario: Multiple invalid files are reported together
- **WHEN** two tracked Python files contain independent syntax errors
- **THEN** stderr identifies both repository-relative paths and their diagnostics, and the check
  exits non-zero

#### Scenario: Parsing is side-effect free
- **WHEN** the parse check runs on valid Python files
- **THEN** it leaves no `__pycache__` directory or compiled bytecode artifact in the repository
