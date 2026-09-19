## ADDED Requirements

### Requirement: Path probes drop a pytest node-id suffix
`extract_probes` SHALL strip a pytest node-id suffix -- a `::`-separated tail such as
`::test_name` or `::TestClass::test_name` -- from a token before deciding whether it is a path
probe, and SHALL emit the remaining file portion as the probe. A token whose file portion is not
itself path-shaped SHALL NOT become a path probe.

#### Scenario: a node-id yields its file as the probe
- **WHEN** the text cites `tests/workqueue/test_premise_check.py::test_path_needle`
- **THEN** the extracted path probe is `tests/workqueue/test_premise_check.py` and the node-id
  form is not emitted

#### Scenario: a class-qualified node-id yields the same file
- **WHEN** the text cites `tests/router/test_preflight.py::TestCommandTargetRepo::test_git_c`
- **THEN** the extracted path probe is `tests/router/test_preflight.py`

#### Scenario: a bare node-id-shaped token is not a path
- **WHEN** the text cites `Foo::bar`, whose portion before `::` is not path-shaped
- **THEN** no path probe is emitted for it

### Requirement: A non-existent, non-filename-shaped path needle produces no verdict
`run_premise_check` SHALL emit no result row for a `path` needle that both does not exist in the
target repo and is not filename-shaped -- filename-shaped meaning its last segment carries a
recognized extension, or it ends in `/`. This SHALL apply in both polarities: such a needle is
neither reported unconfirmed under a presence reading nor reported as a confirmed absence under
an absence reading.

#### Scenario: slash-joined prose is dropped
- **WHEN** a brief's focus says the harness may be `claude/codex/opencode` and no such path
  exists in the repo
- **THEN** `run_premise_check` returns no row for `claude/codex/opencode`

#### Scenario: a two-word either/or is dropped
- **WHEN** a brief's focus says the choice is `stub/disable` and no such path exists
- **THEN** `run_premise_check` returns no row for `stub/disable`

#### Scenario: prose in an absence window is not confirmed as an absence
- **WHEN** the same prose token appears in an absence window, e.g. "there is no `stub/disable`
  handling"
- **THEN** no row is emitted, and in particular no row claiming "absence confirmed"

### Requirement: Real path needles keep their existing verdicts
The shape gate SHALL NOT change the verdict for any needle that is a genuine path claim. A path
that exists SHALL still confirm, including its `:LINE` line-count refinement; a filename-shaped
path that does not exist SHALL still refute under a presence reading and still confirm under an
absence reading; and needles of kind `quoted` and `command` SHALL be unaffected.

#### Scenario: an existing directory path still confirms
- **WHEN** the needle is `src/worktrail/router` and that directory exists
- **THEN** the result row confirms with "path exists"

#### Scenario: a missing filename-shaped path still refutes
- **WHEN** the needle is `src/worktrail/router/does_not_exist.py`
- **THEN** a row is emitted, unconfirmed, with the "path does not exist" detail

#### Scenario: a node-id needle confirms against its file
- **WHEN** the needle text is `tests/workqueue/test_premise_check.py::test_x` and that test file
  exists
- **THEN** the row confirms against `tests/workqueue/test_premise_check.py`
