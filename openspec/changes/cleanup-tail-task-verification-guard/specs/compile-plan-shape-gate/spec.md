## ADDED Requirements

### Requirement: Cleanup tail tasks authored as verification commands are rejected at compile

The compile step SHALL reject a plan containing a task with `kind: cleanup`
whose title matches an imperative instruction to run something — an
imperative verb (`run`/`execute`) together with either a backticked shell
fragment or a recognizable test/validation command (e.g. `pytest`, `npm`,
`yarn`, `jest`, `mocha`, `tox`, `openspec validate`, `python -m`). The
rejection line SHALL name the task id and instruct the author to retag the
task `[e2e]` instead. A `[cleanup]` task whose title does not match this
pattern SHALL NOT be rejected by this rule, and `[e2e]`/`[docs]` tasks are
never subject to it regardless of their title.

#### Scenario: Verification-bodied cleanup task is rejected

- **WHEN** a `tasks.md` task is tagged `[cleanup]` with title "Run
  `pytest -q` and confirm it is green"
- **THEN** compile exits 1 with a problem line naming that task id and
  instructing the author to use `[e2e]` instead, and no run-plan marker is
  written

#### Scenario: Verification-bodied cleanup task without backticks is still rejected

- **WHEN** a `tasks.md` task is tagged `[cleanup]` with title "Run
  PYTHONPATH=src pytest -q and confirm both are green, then run openspec
  validate --strict" (the live-incident wording, no backticks)
- **THEN** compile exits 1 with a problem line naming that task id

#### Scenario: Genuinely inert cleanup task passes

- **WHEN** a `tasks.md` task is tagged `[cleanup]` with title "Remove debug
  logging left in tasks 1-4"
- **THEN** compile emits no cleanup-verification-mismatch problem for that
  task

#### Scenario: Equivalent e2e task is unaffected

- **WHEN** a `tasks.md` task is tagged `[e2e]` with the same imperative
  title that would reject a `[cleanup]` task
- **THEN** compile emits no cleanup-verification-mismatch problem for that
  task, since `[e2e]` tasks are actually dispatched to a worker

#### Scenario: Docs tail task is unaffected

- **WHEN** a `tasks.md` task is tagged `[docs]` with an imperative-sounding
  title
- **THEN** compile emits no cleanup-verification-mismatch problem for that
  task — this rule is scoped to `kind: cleanup` only

### Requirement: Scope-gap remediation names what each tail kind executes

When `worktrail-compile` reports a task with no file scope after compiling,
the remediation guidance SHALL state what each tail-kind option actually
does at dispatch time — `e2e` spawns a worker and runs commands, `cleanup`
is a journal-only status transition that executes nothing, `docs` likewise
executes nothing — rather than listing the three as interchangeable
alternatives with no distinction.

#### Scenario: Scope-gap error names the distinction

- **WHEN** `worktrail-compile` reports one or more tasks with no file scope
  after compiling
- **THEN** the printed remediation text distinguishes `e2e` (spawns a
  worker, runs commands) from `cleanup`/`docs` (execute nothing), instead of
  presenting `docs/e2e/cleanup` as equivalent choices
