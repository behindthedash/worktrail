## MODIFIED Requirements

### Requirement: Shebang and executable mode agree across platforms
The repository SHALL provide a CI helper that reads each tracked Python file's Git index mode and
indexed content, reporting a non-zero result when a shebang-bearing file is non-executable or an
executable file lacks a shebang. The helper SHALL also report a non-zero result when an executable
Python file with a generic `#!/usr/bin/env python3` shebang contains a PEP 758 unparenthesized
multi-exception handler. The helper SHALL be invoked by the existing required CI job and be
runnable locally with the same command.

#### Scenario: CI and local verification see the same policy violation
- **WHEN** the worktree's permission bits or contents differ from an indexed executable Python
  file that has a generic `python3` shebang and PEP 758 syntax
- **THEN** the helper reports the violation from the index consistently on local platforms and CI

#### Scenario: Existing mode violations remain reported
- **WHEN** a tracked Python file has a shebang/mode disagreement
- **THEN** the helper continues to report the applicable EXE001 or EXE002 diagnostic regardless of
  whether the file uses PEP 758 syntax
