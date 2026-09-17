## Why

`worktrail-run-record scope-review` appends with
`record.setdefault("scope_review", []).append(...)` (`src/worktrail/router/run_record.py:898`).
`setdefault` only fills a *missing* key: a record whose `scope_review` key is present with a
null value (`scope_review: null`, which is exactly what `yaml.safe_dump` writes for `None` and
what round-trips back to `None`, not `[]`) crashes with
`AttributeError: 'NoneType' object has no attribute 'append'`. Every list *reader* in the same
module already tolerates this shape via `record.get(...) or []`; the one writer does not, so a
single null list field makes scope review — a pre-PR gate input — impossible to record.
(Work-queue brief `20260917-110615-run-record-null-list-crash`.)

## What Changes

- The `scope-review` subcommand treats a null (or otherwise empty) `scope_review` value the
  same as a missing key: it starts from an empty list, appends the entry, and saves a real
  list, matching the `record.get(...) or []` idiom the readers use.
- Regression tests cover a record with `scope_review: null` and confirm existing entries are
  still preserved on append.

## Capabilities

### New Capabilities
- `run-record-scope-review`: recording scope-review entries on a run record tolerates a null
  `scope_review` field.

### Modified Capabilities

## Impact

- `src/worktrail/router/run_record.py` (`scope-review` handler, one line).
- `tests/router/test_run_record.py` (new regression tests).
- No CLI, schema, or on-disk format change; records that already hold a list are unaffected.
