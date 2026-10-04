# python314-executable-shebang-policy Specification

## Purpose
Ensure directly executable Python that requires Python 3.14 through PEP 758 syntax cannot select
an older host-default interpreter.

## Requirements
### Requirement: PEP 758 executable files select Python 3.14
Every tracked executable `*.py` file whose indexed source contains a PEP 758 unparenthesized
multi-exception handler SHALL start with `#!/usr/bin/env python3.14`, rather than a generic
`#!/usr/bin/env python3` shebang. The requirement applies to direct shebang execution and does not
change console-script entry points or Git executable modes.

#### Scenario: A PEP 758 script is run directly on a host with an older default Python
- **WHEN** a tracked executable Python file contains a PEP 758 handler and the host's `python3`
  resolves below Python 3.14 while `python3.14` is available
- **THEN** its shebang selects `python3.14` and the source does not fail parsing under the older
  default interpreter

#### Scenario: An executable Python file does not use PEP 758 syntax
- **WHEN** a tracked executable Python file has no PEP 758 unparenthesized multi-exception handler
- **THEN** this requirement does not by itself require that file's generic `python3` shebang to be
  changed

### Requirement: CI rejects the generic-interpreter PEP 758 mismatch
The existing index-based shebang/exec-bit verification SHALL inspect tracked executable Python
source for the combination of a `#!/usr/bin/env python3` shebang and a PEP 758 unparenthesized
multi-exception handler. It SHALL report every matching repository-relative path and exit
non-zero. It SHALL continue to read indexed content, so local and CI results describe the same
committed candidate.

#### Scenario: A violating file is indexed
- **WHEN** an executable tracked Python file has the generic `python3` shebang and a PEP 758
  handler in the index
- **THEN** shebang/exec-bit verification reports that path with an actionable Python-3.14
  interpreter diagnostic and fails

#### Scenario: A compliant PEP 758 file is indexed
- **WHEN** an executable tracked Python file has the `python3.14` shebang and a PEP 758 handler
- **THEN** shebang/exec-bit verification reports no interpreter-policy violation for that file

#### Scenario: The worktree differs from the index
- **WHEN** a local worktree edit removes the PEP 758 handler or changes the shebang without staging
  it
- **THEN** verification continues to evaluate the indexed file content
