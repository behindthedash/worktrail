## ADDED Requirements

### Requirement: Pure classification of a PR's statusCheckRollup
`classify_checks()` SHALL classify a `gh pr view --json statusCheckRollup` rollup into
`(any_pending, [failing check names])` from the rollup contents alone (plus an optional
`required` list of check-context names), with no network or state access of its own, so every
caller — `verify.py`'s own gating and `postmerge-reconciliation-audit`'s sweep — sees identical
classification for the same rollup input.

#### Scenario: Null or empty rollup with no required names
- **WHEN** `classify_checks(None)` or `classify_checks([])` is called with no `required` argument
- **THEN** the system returns `(False, [])`

#### Scenario: A required check name absent from the rollup
- **WHEN** `classify_checks()` is called with a `required` list containing a name not present in
  the rollup
- **THEN** the system reports `any_pending=True`, since a required check that has not yet been
  scheduled or reported must not be read as "nothing to wait on"

### Requirement: A superseded CANCELLED run is not a failure
When a `statusCheckRollup` entry is a `CheckRun` with conclusion `CANCELLED` for a given check
name, and the same rollup also contains a different entry for that identical check name whose
conclusion is not `CANCELLED` (settled or still pending), `classify_checks()` SHALL exclude the
`CANCELLED` entry from both the returned `failing` list and pending determination for that name —
it is stale bookkeeping from a run the provider superseded, not evidence the check itself failed.

#### Scenario: CANCELLED entry superseded by a later SUCCESS for the same name
- **WHEN** a rollup contains one entry named `build` with conclusion `CANCELLED` and a second
  entry also named `build` with conclusion `SUCCESS`
- **THEN** `classify_checks()` does not include `build` in `failing` and does not report pending
  on account of the `CANCELLED` entry

#### Scenario: CANCELLED entry superseded by a still-running retry for the same name
- **WHEN** a rollup contains one entry named `build` with conclusion `CANCELLED` and a second
  entry also named `build` with status `IN_PROGRESS`
- **THEN** `classify_checks()` reports pending on account of the `IN_PROGRESS` entry, and does
  not include `build` in `failing`

#### Scenario: CANCELLED entry with no superseding entry for the same name
- **WHEN** a rollup contains exactly one entry for a check name and its conclusion is `CANCELLED`
- **THEN** `classify_checks()` includes that check name in `failing`, exactly as before this
  change — a genuinely cancelled check with nothing superseding it is still a failure

#### Scenario: Two independent CANCELLED entries with different names
- **WHEN** a rollup contains a `CANCELLED` entry named `build` and a separate `CANCELLED` entry
  named `lint`, each the only entry for its own name
- **THEN** both `build` and `lint` are included in `failing` — the exemption only applies within
  the same check name, not across unrelated checks
