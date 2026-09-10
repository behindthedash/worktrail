## ADDED Requirements

### Requirement: Inline dependency declaration parsing

The OpenSpec checklist parser SHALL recognize an indented `depends:` continuation line in
the same window beneath a `- [ ] N.M` task line where `files:` and `review:` are recognized,
extract its comma- or whitespace-separated task ids, and carry them through task loading as
additional entries in that task's dependency list, unioned with the format's baseline
within-group ordering. A self-reference SHALL be dropped. A second `depends:` line under the
same task, or one naming no ids, SHALL produce a parser warning and be otherwise ignored, as
the `files:` line does. An id naming no task in the change SHALL be reported by the task
source's existing dependency validation.

#### Scenario: Declared dependency reaches the loaded task

- **WHEN** a `tasks.md` task `2.1` is followed by an indented `depends: 1.1` line and `1.1`
  is in a different group
- **THEN** the loaded task `2.1` carries `1.1` in its dependencies alongside any baseline
  within-group predecessor

#### Scenario: A file-disjoint consumer is ordered by its declaration alone

- **WHEN** task `2.1` declares `depends: 1.1`, and the two tasks' `files:` share no path and
  neither file exists on disk yet
- **THEN** the compiled plan records `1.1` in `2.1`'s dependencies

#### Scenario: An unknown id is reported

- **WHEN** a task declares `depends: 9.9` and no task `9.9` exists in the change
- **THEN** dependency validation reports the unresolvable reference naming the task and the
  id

#### Scenario: Declaration survives status write-back

- **WHEN** a task's checkbox is ticked in place after being parsed with a `depends:` line
- **THEN** only the checkbox marker changes; the `depends:` line is left untouched
