## Purpose

Reconciles work-queue briefs with individual spec/openspec-change tasks in both directions: preventing a new brief from duplicating an already-open task, and catching closure-time drift between a brief marked done and its target task's checkbox state.

## ADDED Requirements

### Requirement: Task-Level Candidate Enumeration Scoped To A Known Target
The system SHALL enumerate a target OpenSpec change's individual open (unchecked) tasks as match
candidates only when a caller supplies that change's id as an explicit target. The system SHALL
NOT enumerate task candidates across changes the caller did not name as the target, and SHALL NOT
perform a fleet-wide task scan when no target is supplied.

#### Scenario: Target change supplied with open tasks
- **WHEN** a caller supplies an active OpenSpec change id that has one or more unchecked tasks in
  its `tasks.md`
- **THEN** the returned candidate set includes one entry per unchecked task, in addition to the
  existing whole-change candidate

#### Scenario: No target supplied
- **WHEN** a caller performs a candidate lookup with no target change id
- **THEN** the returned candidate set is exactly today's whole-spec/whole-change candidates, with
  no per-task entries and no scan of any change's `tasks.md`

#### Scenario: Devkit-shaped target is not task-enumerated
- **WHEN** a caller supplies a devkit-shaped spec id (a `docs/specs/NNN-slug` directory) as the
  target
- **THEN** the returned candidate set is the existing whole-spec candidate only; no per-task
  entries are added, since devkit's per-task `TASK-*.md` file representation is out of scope for
  this capability

### Requirement: Task Candidate Shape
Each task-level candidate SHALL include the task's id, its literal task line text, and its
current checked/unchecked state, so a caller can both judge the semantic match and report the
matched task id without a second lookup.

#### Scenario: Task candidate fields present
- **WHEN** a task-level candidate is returned
- **THEN** it includes a task id (e.g. `2.3`), the task's line text, and `checked: false`

### Requirement: Dispatch-Time Guard Distinguishes Task-Level Matches From Whole-Spec Matches
When the dispatch-time collision guard (Routes C, D, F, or G) judges a strong match against a
task-level candidate, the system SHALL treat it as distinct from a whole-spec `Implemented`
match: it SHALL NOT auto-close a Route C/D brief the way a confirmed whole-spec `Implemented`
match does, since the matched task is not yet complete. It SHALL instead surface the matched
change id and task id so the dispatch can be redirected to track the existing task rather than
proceed as independent duplicate scope.

#### Scenario: Route D dispatch matches an open task in a different change than its own target
- **WHEN** a Route D dispatch's resolved request is judged a strong match against an open,
  unchecked task in an active OpenSpec change other than the dispatch's own `target-spec`
- **THEN** the guard does not auto-close the brief as if the work were already shipped, and
  surfaces the matched change id and task id instead of proceeding to dispatch independent
  duplicate implementation work

#### Scenario: No task-level match found
- **WHEN** the dispatch-time guard's task-level candidates contain no strong match
- **THEN** dispatch proceeds unmodified, exactly as when the guard finds no whole-spec match today

### Requirement: Dashboard Advisory Surfaces Brief-vs-Task Matches
The dashboard's advisory brief-clustering scan SHALL surface a match between a queued brief that
carries `target-spec:` and an open, unchecked task in that target spec/change, using the same
reporting surface as its existing brief-vs-brief duplicate clusters, so an undispatched brief
that duplicates an already-tracked task is visible before either is claimed.

#### Scenario: Two undispatched briefs target the same open task
- **WHEN** two queued briefs both carry the same `target-spec:` and their focus text strongly
  matches the same open, unchecked task in that change
- **THEN** the dashboard surfaces an advisory identifying the shared task, without blocking either
  brief from being claimed

#### Scenario: A queued brief matches its own already-referenced task
- **WHEN** a queued brief already carries `target-task:` naming the task its focus matches
- **THEN** no new advisory is surfaced for that pairing, since the brief already declares the
  relationship

### Requirement: `target-task` Frontmatter Field
The work-queue brief schema SHALL support an optional `target-task:` field naming a single task
id within the brief's `target-spec:`. The field SHALL be validated on write to be non-empty and
free of characters that would make it ambiguous with a task id in another change; a brief without
`target-task:` SHALL behave exactly as before this change. Writing `target-task:` SHALL NOT
require or trigger rewriting any other existing brief.

#### Scenario: Brief created with both fields
- **WHEN** a brief is created with `target-spec: my-change` and `target-task: 2.3`
- **THEN** both fields are persisted in the brief's frontmatter unchanged

#### Scenario: Brief created with only target-spec
- **WHEN** a brief is created with `target-spec:` set and no `target-task:`
- **THEN** the brief is created exactly as before this change, with no `target-task:` field

#### Scenario: Empty target-task rejected
- **WHEN** a caller supplies an empty or whitespace-only `target-task:` value
- **THEN** brief creation fails before a brief is written, identifying the invalid value

### Requirement: Closure-Time Checkbox-Sync Check
When `work_queue.py done` closes a brief as implementation-complete and that brief carries both
`target-spec:` and `target-task:`, the system SHALL check the referenced task's current
checkbox state in the target spec's `tasks.md` before completing the closure.

#### Scenario: Closing brief with both fields set
- **WHEN** a brief carrying `target-spec: my-change` and `target-task: 2.3` is closed with
  `--implementation-complete`
- **THEN** the system reads task `2.3`'s current checkbox state in `my-change`'s `tasks.md` as
  part of the closure

#### Scenario: Closing brief with no target-task
- **WHEN** a brief with no `target-task:` field is closed
- **THEN** no checkbox-state check is performed, and closure proceeds exactly as before this
  change

### Requirement: Checkbox-Sync Warns And Never Modifies The Target Spec
When the checkbox-sync check finds the referenced task still unchecked at closure time, the
system SHALL surface `checkbox_out_of_sync: true` in the `done` result and record the mismatch in
the brief's closure note. The system SHALL NOT modify the target spec's `tasks.md` as part of
this check.

#### Scenario: Task still unchecked at closure
- **WHEN** the checkbox-sync check finds the referenced task's checkbox unticked
- **THEN** the `done` result includes `checkbox_out_of_sync: true`, the closure note records the
  mismatch, and the target spec's `tasks.md` file is not written to

#### Scenario: Task already checked at closure
- **WHEN** the checkbox-sync check finds the referenced task's checkbox already ticked
- **THEN** the `done` result does not include `checkbox_out_of_sync: true`, and closure proceeds
  with no additional note

### Requirement: Checkbox-Sync Lookup Is Best-Effort And Never A Fresh Model Call
The checkbox-sync check SHALL read task state via a cached lookup only (no invocation of a model
to re-derive task file scope or state). A cache miss, unreadable `tasks.md`, or missing target
spec directory SHALL degrade to "no signal" — the check is skipped and closure proceeds
unmodified — rather than blocking or failing the closure.

#### Scenario: No cached lookup available
- **WHEN** the target spec/change has no cached data available for the checkbox-sync check
- **THEN** closure proceeds exactly as if no `target-task:` were present, and no warning is
  surfaced

#### Scenario: Target spec directory missing
- **WHEN** the brief's `target-spec:` no longer resolves to an existing spec/change directory
- **THEN** the checkbox-sync check degrades to no signal and closure is not blocked
