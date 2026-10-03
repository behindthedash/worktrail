## Purpose

Stop a below-floor Python interpreter from reaching orchestrated work as an opaque parse failure.
Worktrail resolves the interpreters a target repository's commands will actually run — every
interpreter its declared gate commands name, plus the `python3` a shell resolves on PATH — and
checks each against the repository's declared `requires-python` floor before any orchestrator or
worker is launched, so a version mismatch surfaces as one actionable message instead of a
downstream `SyntaxError` and quarantined groups.

## ADDED Requirements

### Requirement: The check enforces the target repository's declared Python floor
`worktrail-check-interpreter-floor` SHALL resolve the interpreter floor from the target
repository's `pyproject.toml` `[project] requires-python` and SHALL enforce the highest `>=`
lower bound that declaration carries. When the repository has no `pyproject.toml`, no
`requires-python` value, or no `>=` lower bound, the check SHALL report that no floor is
enforceable and SHALL exit successfully.

#### Scenario: A repository declares a lower bound
- **WHEN** the target repository's `pyproject.toml` declares `requires-python = ">=3.14"`
- **THEN** the check's floor is 3.14 and every resolved interpreter below it is a finding

#### Scenario: Several lower bounds
- **WHEN** the declaration reads `requires-python = ">=3.10,<4"`
- **THEN** the enforced floor is 3.10

#### Scenario: No enforceable floor
- **WHEN** the target repository has no `pyproject.toml`, or its `requires-python` carries no
  `>=` lower bound (absent, empty, or a non-comparable specifier)
- **THEN** the check reports that no floor is enforceable and exits zero

### Requirement: The checked interpreters are the ones the repository's commands and a shell will resolve
The check SHALL identify every Python interpreter command word appearing in the repository's
declared `pre_pr_cmd`, `pre_commit_cmd`, and `integrate_smoke_cmd` (as resolved by the policy
loader) and SHALL resolve each through PATH the way a shell would, including command words that
follow leading `VAR=value` assignments and `&&`-chained segments. It SHALL also resolve the
ambient `python3` when PATH provides one. Command words that do not name a Python interpreter
SHALL NOT be checked, and the gate commands SHALL NOT be executed. A gate-command interpreter
that PATH does not resolve SHALL be a finding; an ambient `python3` that PATH does not resolve
SHALL NOT be.

#### Scenario: The ambient shell interpreter is below the floor
- **WHEN** `python3` on PATH resolves to a version below the repository's floor
- **THEN** the check reports a finding naming `python3`, its resolved path, and its version

#### Scenario: A gate command pins its interpreter
- **WHEN** a declared gate command reads `PYTHONPATH=src python3.14 -m pytest -q`
- **THEN** `python3.14` is checked, and `PYTHONPATH`, `pytest`, and any non-interpreter word in
  the command are not treated as interpreters

#### Scenario: A declared gate interpreter is missing
- **WHEN** a gate command names `python3.14` and PATH provides no `python3.14`
- **THEN** the check reports a finding that the declared command cannot run

#### Scenario: No ambient python3 exists
- **WHEN** PATH provides no `python3` and no gate command names one
- **THEN** the absence is not a finding

### Requirement: Version probing never fails at parse time and executes no repository code
For each resolved interpreter the check SHALL determine the version by executing that
interpreter with a standalone expression that an interpreter older than the floor can still
parse, and SHALL bound how long it waits. It SHALL NOT import or execute repository code, and
SHALL NOT run any part of the repository's gate commands. A resolved interpreter whose version
cannot be determined SHALL be a finding.

#### Scenario: An older interpreter yields its version
- **WHEN** the resolved `python3` is older than the floor
- **THEN** the check reports that interpreter's actual version rather than failing to parse

#### Scenario: A probe cannot determine a version
- **WHEN** a resolved interpreter exits non-zero, times out, or prints nothing a version can be
  parsed from
- **THEN** the check reports the interpreter as undetermined rather than passing it

### Requirement: Findings are actionable, complete, and side-effect free
The check SHALL report every finding in one invocation, each naming the interpreter word, its
resolved path or that PATH provides none, its version or that it could not be determined, the
target repository, and the declared `requires-python` specifier, followed by a remediation that
names at least one concrete fix: make the ambient `python3` resolve to an interpreter that
satisfies the floor, install the missing interpreter, or pin the affected command to an
interpreter that does. It SHALL exit non-zero when any finding exists and zero when none does,
and `--json` SHALL emit the same report machine-readably. The check SHALL NOT modify the
repository, create files, or execute gate commands.

#### Scenario: A below-floor environment fails loudly
- **WHEN** the floor is 3.14 and `python3` resolves to 3.12.3
- **THEN** the output names `python3`, its path, 3.12.3, the declared floor, and the
  remediation, and the check exits non-zero

#### Scenario: Every finding is reported together
- **WHEN** two checked interpreters fail for independent reasons
- **THEN** both findings appear in the single invocation's output

#### Scenario: A compliant environment passes
- **WHEN** every checked interpreter satisfies the floor
- **THEN** the check reports the checked interpreters and exits zero without touching the tree

### Requirement: Orchestrator and worker launches refuse a below-floor environment
`worktrail-live full-real` SHALL run the check for its target repository and SHALL refuse to
launch — before creating any worktree, run journal, or worker — when the check reports findings,
printing them and exiting non-zero. `worktrail-live precheck` SHALL report the same findings
ahead of its task-DAG diagnostics, under a label that distinguishes the environment abort from a
task-DAG warning, and SHALL exit non-zero. An environment that passes SHALL leave both commands'
behaviour otherwise unchanged.

#### Scenario: A launch is refused
- **WHEN** `worktrail-live full-real` targets a repository whose resolved interpreters do not
  satisfy its declared floor
- **THEN** no worktree, journal, or worker is created, the findings and remediation are printed,
  and the command exits non-zero

#### Scenario: The pre-dispatch gate reports the floor first
- **WHEN** `worktrail-live precheck` runs against a repository whose resolved interpreters are
  below the declared floor
- **THEN** it prints the findings under the interpreter-floor label before any task-DAG
  diagnostic and exits non-zero

#### Scenario: A compliant environment launches unchanged
- **WHEN** every checked interpreter satisfies the declared floor, or the repository declares no
  enforceable floor
- **THEN** `full-real` and `precheck` behave as they do today
