## ADDED Requirements

### Requirement: OpenSpec Per-Task Candidate Enumeration
When a caller supplies an OpenSpec change id as an explicit target, the system SHALL be able to
return one candidate per unchecked task in that change's `tasks.md`, each with the same
`{task_id, task_text, checked}` shape, in addition to the existing whole-change candidate. This
mode SHALL only run for the supplied target change; it SHALL NOT be invoked as a scan across
every change under an OpenSpec root.

#### Scenario: Target change with unchecked tasks
- **WHEN** a caller requests per-task candidates for an OpenSpec change id whose `tasks.md`
  contains unchecked task lines
- **THEN** one candidate is returned per unchecked task, each carrying its task id, task line
  text, and `checked: false`

#### Scenario: Target change fully checked
- **WHEN** a caller requests per-task candidates for an OpenSpec change id whose `tasks.md` has
  no unchecked tasks
- **THEN** an empty per-task candidate list is returned, with no error

#### Scenario: No target supplied
- **WHEN** a caller performs the existing whole-root `scan()` with no target change id
- **THEN** behavior is unchanged from before this requirement — whole-change candidates only, no
  per-task entries
