## ADDED Requirements

### Requirement: Inline worker-budget declaration parsing

The OpenSpec checklist parser SHALL recognize an indented `timeout:` continuation line in the
same window beneath a `- [ ] N.M` task line where `files:`, `review:` and `depends:` are
recognized, extract its value as a positive whole number of seconds, and carry it through task
loading as that task's declared worker-timeout budget — the same field devkit frontmatter
`timeout:` reaches for the devkit format. A value that is not a positive whole number of
seconds SHALL produce a parser warning and be treated as undeclared, as SHALL an empty value,
and as SHALL a second `timeout:` line under the same task (keeping the first), matching the
tolerant-parse posture the sibling declaration lines already have. A task line with no
`timeout:` continuation line SHALL load with no declared budget, and a top-level unindented
`timeout:` line SHALL NOT be read as a declaration.

#### Scenario: A declaration under a task becomes that task's budget

- **WHEN** a `tasks.md` task line is followed by an indented `timeout:` line naming a positive
  number of seconds
- **THEN** that number is the loaded task's declared worker-timeout budget, in the same
  normalized form the per-task override resolution already reads

#### Scenario: Task without a declaration is unaffected

- **WHEN** a `tasks.md` task line has no indented `timeout:` continuation line
- **THEN** the task loads with no declared budget and every downstream behavior matches a
  pre-declaration parse of the same content

#### Scenario: A value that is not a positive whole number of seconds warns and is not applied

- **WHEN** an indented `timeout:` line names an unparseable value, a zero or negative value, or
  nothing at all
- **THEN** the parser records a warning naming the task and the line, and the task loads with no
  declared budget rather than with that value

#### Scenario: Duplicate declarations

- **WHEN** more than one indented `timeout:` line follows the same task line
- **THEN** the parser records a warning naming the task and the duplicate, and the first
  declaration's value is the one carried through

#### Scenario: Declaration coexists with the sibling lines

- **WHEN** a task line is followed by an indented `files:` line and an indented `timeout:` line
  in either order
- **THEN** the parsed task carries both the declared file scope and the declared budget

#### Scenario: A top-level timeout line is not a declaration

- **WHEN** a `timeout:` line appears without indentation, outside any task's continuation
  window
- **THEN** it is not read as a task's budget and no task's declared budget changes

### Requirement: A declared budget is the worker budget for its task

A task loaded with a declared budget SHALL have it override the run-wide worker timeout for that
task alone, through the existing per-task override resolution and with no new mechanism: a
declared budget is the value handed to that task's headless spawn, it takes precedence over the
run-wide default and over a run-wide override, and every sibling task in the same run keeps the
run-wide value. A task whose kind is a tail verification kind and which declares a budget SHALL
NOT be advised to declare one, because the advisory's existing guard tests exactly that field.

#### Scenario: The declared budget is what the worker is spawned with

- **WHEN** a run with a run-wide worker timeout is dispatched over a task that declared its own
  budget
- **THEN** that task's worker is spawned with the declared budget, and the task's start banner
  and timeout message report the declared value

#### Scenario: Sibling tasks keep the run-wide timeout

- **WHEN** one task in a run declares a budget and another does not
- **THEN** the declaring task uses its declared value and the other uses the run-wide value

#### Scenario: A tail task with a declared budget gets no run-wide-default advice

- **WHEN** a tail verification task that declared a budget is killed at that budget
- **THEN** the run-wide worker timeout advisory is not printed for it, because the run-wide
  default is not the budget it was running on

#### Scenario: No declared budget falls back exactly as before

- **WHEN** a tail verification task with no declared budget is killed at the run-wide timeout
- **THEN** it is marked failed as today and the advisory naming the per-task field and the
  run-wide override is printed

### Requirement: Authoring documentation of the budget declaration

The bundled `openspec-propose` skill's tasks-artifact guidance SHALL document the optional
indented `timeout:` declaration — its syntax, that it is opt-in per task, that its value is a
number of seconds, and that a task which declares none runs on the run-wide worker timeout
instead. The guidance SHALL state when a declaration is expected: a task whose body predictably
outruns the run-wide worker timeout — notably a tail `[e2e]` task that runs the repository's
whole test suite together with the lint, golden and targeted-suite commands its body names —
declares a budget sized to that work, rather than being left to fail on the default.

#### Scenario: Skill guidance presents the syntax and its optionality

- **WHEN** the tasks-artifact guidance is consulted while authoring a `tasks.md`
- **THEN** it presents the indented `timeout:` continuation form with an example, states that
  its value is seconds, and states that omitting it leaves the task on the run-wide worker
  timeout

#### Scenario: Skill guidance names the task that must declare one

- **WHEN** the tasks-artifact guidance is consulted while authoring a tail `[e2e]` verification
  task whose body runs the repository's whole suite
- **THEN** it directs the author to declare a `timeout:` sized to that suite rather than leaving
  the task on the run-wide default
