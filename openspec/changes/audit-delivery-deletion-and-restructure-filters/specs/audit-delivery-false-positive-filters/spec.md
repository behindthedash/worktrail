## ADDED Requirements

### Requirement: Deliberate deletion is classified as delivered
`worktrail-audit-delivery` SHALL treat a path that a task's own commit deleted as delivered
when that path is absent on the base branch. A reviewed-PASSED task whose commit is not an
ancestor of base and whose shippable diff consists only of deletions, all absent on base,
SHALL be reported under `content_delivered_via_deletion`, not `confirmed_dropped`. A task
whose diff both adds content and deletes paths SHALL be evaluated by the existing rewrite
check with each deleted-and-absent path counted as delivered. A deleted path that still
exists on base SHALL NOT count as delivered.

#### Scenario: Pure-deletion task whose file is gone on base
- **WHEN** a task's commit only deletes `old_module.py`, the commit is not an ancestor of
  base, and base has no `old_module.py`
- **THEN** the record appears in `content_delivered_via_deletion` and not in
  `confirmed_dropped`

#### Scenario: Mixed add-and-delete task delivered via squash
- **WHEN** a task's commit deletes `old.py` and adds `new.py`, base has no `old.py`, and
  base contains `new.py` with matching content
- **THEN** the record appears in `content_delivered_via_rewrite`

#### Scenario: Deletion that did not land stays dropped
- **WHEN** a task's commit only deletes `keep.py` but base still contains `keep.py`
- **THEN** the record appears in `confirmed_dropped`

### Requirement: Restructured paths are excused only when opted in
`worktrail-audit-delivery` SHALL accept a repeatable `--restructured-path GLOB` option. When
one or more globs are given, a task that fails the rewrite and identifier checks SHALL be
reported under `superseded_by_restructure` if every shippable file is either
(a) absent on base and matches at least one glob, or (b) individually content-verified on
base. A glob-matched path that still exists on base SHALL NOT be excused. When no globs are
given, classification SHALL be unchanged.

#### Scenario: Migration file orphaned by a baseline squash
- **WHEN** the audit runs with `--restructured-path 'api/migrations/versions/*.py'` and a
  task's only shippable file is `api/migrations/versions/0042_add_col.py`, absent on base
- **THEN** the record appears in `superseded_by_restructure`

#### Scenario: Matched path still present on base is not excused
- **WHEN** the audit runs with `--restructured-path 'api/app/models.py'` and base still
  contains `api/app/models.py` with content that fails the rewrite check
- **THEN** the record appears in `confirmed_dropped`

#### Scenario: No globs given leaves classification unchanged
- **WHEN** the audit runs without `--restructured-path` against the same orphaned migration
  file
- **THEN** the record appears in `confirmed_dropped`

### Requirement: New buckets are reported but never fail the run
The JSON result and text summary SHALL include `content_delivered_via_deletion` and
`superseded_by_restructure` counts alongside the existing buckets. The exit code SHALL be
non-zero only when `confirmed_dropped` is non-empty.

#### Scenario: Only new-bucket findings exit zero
- **WHEN** a run's only non-delivered findings fall in `content_delivered_via_deletion` or
  `superseded_by_restructure`
- **THEN** both counts appear in the summary output and the exit code is 0
