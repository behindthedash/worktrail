## Purpose

Stop an interpreter that cannot run the gate's own tooling from reaching an orchestrated fan-out.
Worktrail resolves the interpreter words its policy gates (`integrate_smoke_cmd`, `pre_pr_cmd`,
`pre_commit_cmd`) will actually run through PATH, verifies that each can run the tools its command
invokes, and refuses the launch before any worker is spawned — so a repository whose PATH resolves
to an interpreter lacking its development dependencies surfaces as one actionable message instead
of the observed `No module named pytest`, ~16 minutes of fan-out, and every group quarantined.

## ADDED Requirements

### Requirement: The check verifies the tools each gate command invokes are available to the interpreter that command resolves
`worktrail-check-gate-interpreter` SHALL identify, for each of the target repository's declared
`pre_pr_cmd`, `pre_commit_cmd`, and `integrate_smoke_cmd`, every Python interpreter command word
appearing in it — including a word following a leading `VAR=value` assignment and a word in a
segment chained by `&&`, `||`, `;`, or `|` — and SHALL resolve each such word through PATH the way
a shell would. For each resolved interpreter it SHALL identify every tool that its segment asks
that interpreter to run via `-m <module>` and SHALL verify that the tool is available to that
resolved interpreter. Command words that do not name a Python interpreter SHALL NOT be resolved,
and a gate command that invokes no tool through `-m` SHALL contribute nothing to the check.

#### Scenario: A gate command's interpreter lacks the tool it is asked to run
- **WHEN** `integrate_smoke_cmd` reads `PYTHONPATH=src python3.14 -m pytest -q` and the
  `python3.14` that PATH resolves cannot provide `pytest`
- **THEN** the check reports a finding naming `python3.14`, its resolved path, the tool `pytest`,
  and the `integrate_smoke_cmd` declaration that named it

#### Scenario: Non-interpreter words and script paths are not checked
- **WHEN** a gate command reads `python3.14 scripts/ci/ruff_pinned.py check . && bash -c "true"`
- **THEN** no interpreter is resolved from `scripts/ci/ruff_pinned.py` or `bash`, and no tool is
  probed for that command

#### Scenario: A gate command invokes no tool through `-m`
- **WHEN** a declared gate command names no `-m <module>` invocation
- **THEN** the check makes no finding about that command and exits zero

#### Scenario: An interpreter the gate names is not on PATH
- **WHEN** a gate command names `python3.14` and PATH provides no `python3.14`
- **THEN** this check makes no finding for that word, because a missing or below-floor interpreter
  is the finding of the interpreter-floor check rather than a tool-availability finding

### Requirement: Availability is determined without executing gate commands or repository code
The check SHALL reduce each `-m` target to its top-level module name and SHALL determine its
availability by locating that module for the resolved interpreter — for example with
`importlib.util.find_spec` — rather than importing it, so that no module-level code of the target
repository runs. The check SHALL NOT run any part of a gate command, and SHALL bound how long it
waits for a probe. A probe that cannot determine availability SHALL be reported as undetermined
rather than passing.

#### Scenario: A repository-local target is located, not imported
- **WHEN** a gate command reads `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`
- **THEN** the check probes the top-level name `worktrail` without importing the package and
  without running any part of the command

#### Scenario: A probe cannot determine availability
- **WHEN** a probe's interpreter exits non-zero for a reason other than the module being absent,
  times out, or emits nothing the check can read
- **THEN** the check reports that tool as undetermined rather than passing it

### Requirement: Findings are actionable, complete, and side-effect free
The check SHALL report every finding in one invocation, each naming the interpreter word, its
resolved path, the tool, the policy key and command that named the tool, the target repository,
and a remediation that names at least one concrete fix: put the repository-local interpreter on
PATH — naming `<repo>/.venv/bin` when that directory provides a matching interpreter — install the
missing tool into the interpreter the command resolves, or point the affected command at an
interpreter that provides it. It SHALL exit non-zero when any finding exists and zero when none
does, and `--json` SHALL emit the same report machine-readably. The check SHALL NOT modify the
repository, create files, or execute a gate command.

#### Scenario: A gate-incapable environment fails loudly
- **WHEN** a gate command resolves `python3.14` to an interpreter that cannot provide `pytest`
- **THEN** the output names `python3.14`, its path, `pytest`, the declaring command, and the
  remediation, and the check exits non-zero

#### Scenario: Every finding is reported together
- **WHEN** two gate commands name interpreters that are each missing a different tool
- **THEN** both findings appear in the single invocation's output

#### Scenario: A gate-capable environment passes
- **WHEN** every tool every resolved gate interpreter is asked to run is available
- **THEN** the check reports what it checked and exits zero without touching the tree

### Requirement: Orchestrated launches refuse a gate environment that cannot run its own gates
`worktrail-live full-real` SHALL run the check for its target repository and SHALL refuse to
launch — before taking the run lock and before creating any worktree, run journal, or worker —
when the check reports findings, printing them and exiting non-zero. An environment that passes
the check, and a repository whose gate commands name no tool the check can verify, SHALL leave
`full-real`'s behaviour otherwise unchanged.

#### Scenario: A launch is refused
- **WHEN** `worktrail-live full-real` targets a repository whose gate commands resolve an
  interpreter that cannot run the tools those commands invoke
- **THEN** no worktree, journal, or worker is created, the findings and remediation are printed,
  and the command exits non-zero

#### Scenario: A compliant environment launches unchanged
- **WHEN** every tool the target repository's gate commands invoke is available to the interpreter
  each command resolves, or its commands name no tool the check can verify
- **THEN** `full-real` proceeds exactly as it does today
