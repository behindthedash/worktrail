## Purpose

Lets an author of an OpenSpec change declare per-task file scope inline in `tasks.md`, so
`worktrail-compile` can seed the RunPlan from the artifact alone — no model call — instead of
inferring scope it may be unable to (provider-gated compile), and so its "add explicit
`files:` scope to the artifact" remediation is actionable for this format.

## ADDED Requirements

### Requirement: Inline file-scope declaration parsing

The OpenSpec checklist parser SHALL recognize an indented `files:` continuation line
directly beneath a `- [ ] N.M` task line, extract its repo-relative path tokens into that
task's parsed file list, and carry them through task loading as the task's declared file
scope — exactly as devkit frontmatter `files:` reaches the same field for the devkit
format. A task line without a continuation line SHALL parse with an empty declared file
list, unchanged from before this declaration existed.

#### Scenario: Declaration under a task is parsed into the task's file scope

- **WHEN** a `tasks.md` task line is followed by an indented `files:` line naming one or
  more repo-relative paths
- **THEN** each named path appears in that task's loaded file scope, in the same
  normalized form downstream consumers (`needs_compile`, RunPlan seeding) already read

#### Scenario: Task without a declaration is unaffected

- **WHEN** a `tasks.md` task line has no indented `files:` continuation line
- **THEN** the task parses with an empty declared file list and every downstream behavior
  matches a pre-declaration parse of the same content

#### Scenario: Declaration survives status write-back

- **WHEN** a task's checkbox is ticked in place after being parsed with a declaration
- **THEN** only the checkbox marker changes; the indented `files:` line and all other
  bytes of the file are left untouched

### Requirement: Declared scope satisfies compilation without a model call

When every non-tail task in a change carries at least one declared file, plan compilation
SHALL produce the change's RunPlan purely from the parsed artifact — recording it as
seeded-from-artifact — without any model invocation, matching the free path devkit specs
already take. A change where only some tasks declare files SHALL still have its declared
scopes honored while remaining undeclared tasks go through the existing inference path.

#### Scenario: Fully declared change takes the no-model seed path

- **WHEN** plan compilation runs against a change whose implementation tasks all declare
  at least one file (tail-kind tasks need none)
- **THEN** the produced plan's per-task file scopes equal the declared lists, the plan is
  recorded as seeded rather than model-inferred or baseline, and no inference call is made

#### Scenario: Partially declared change still compiles the gaps

- **WHEN** some implementation tasks declare files and others do not
- **THEN** the declaring tasks' scopes are used as declared, and only the undeclared tasks'
  scopes are subject to inference — with the conservative baseline behavior unchanged when
  inference is unavailable

#### Scenario: Editing a declaration invalidates the cached plan

- **WHEN** a change's cached plan exists and a task's declaration is subsequently edited
- **THEN** the next compilation does not serve the stale cache entry, because the change's
  planning fingerprint incorporates the declared file lists

### Requirement: Tolerant handling of malformed declarations

A malformed `files:` declaration SHALL degrade to a reported warning plus treatment as if
undeclared — never a hard parse error, matching `tasks.md`'s existing tolerant-parse
posture, since OpenSpec itself silently ignores lines it does not track.

#### Scenario: Declaration names no paths

- **WHEN** an indented `files:` line directly beneath a task names no paths
- **THEN** the parser records a warning identifying the task and line, and the task loads
  with an empty declared file list

#### Scenario: Duplicate declarations for one task

- **WHEN** more than one indented `files:` line follows the same task line
- **THEN** the parser records a warning identifying the task and the duplicate, and the
  first declaration's paths are the ones carried through

### Requirement: Authoring documentation of the declaration syntax

The bundled `openspec-propose` skill's tasks-artifact guidance SHALL document the optional
declaration syntax — including that it is opt-in per task, what belongs in it (paths the
task creates or modifies, not reads), and that leaving it out keeps today's inferred-scope
behavior.

#### Scenario: Skill guidance shows the syntax and its optionality

- **WHEN** the tasks-artifact guidance is consulted while authoring a `tasks.md`
- **THEN** it presents the indented `files:` continuation form with an example, states
  that a task may omit it, and states that a fully declared change compiles to its run
  plan without a model call
