# run-record-scope-review Specification

## Purpose
Makes `worktrail-run-record scope-review` treat a null `scope_review` value the same as a missing key,
appending to an empty list and preserving existing entries in order. Prevents a scope-review append
from crashing on a record that was written with an explicit null.
## Requirements
### Requirement: Scope-review append tolerates a null list field
`worktrail-run-record scope-review` SHALL treat a run record whose `scope_review` key is
present with a null value the same as a record with no `scope_review` key: it SHALL append the
new `<status> | <item> | <detail>` entry to an empty list, save `scope_review` as a list, and
exit 0. It SHALL NOT raise on a null `scope_review`. When `scope_review` already holds a list,
existing entries SHALL be preserved in order and the new entry appended after them.

#### Scenario: Null scope_review is replaced by a list
- **WHEN** a run record contains `scope_review: null` and `scope-review` is run with status
  `addressed` for item `README`
- **THEN** the command exits 0 and the saved record's `scope_review` is a one-element list
  holding the new entry

#### Scenario: Missing scope_review still works
- **WHEN** a run record has no `scope_review` key and `scope-review` is run
- **THEN** the command exits 0 and the saved record's `scope_review` is a one-element list

#### Scenario: Existing entries are preserved
- **WHEN** a run record's `scope_review` already holds one entry and `scope-review` is run
  for a second item
- **THEN** the saved `scope_review` holds the original entry followed by the new one

