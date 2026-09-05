## Why

`src/worktrail/orchestrator/verify.py:120-128` defines `_FAIL_CONCLUSIONS` including
`"CANCELLED"`, and `classify_checks()` (`verify.py:267-304`, specifically line 294) appends any
`CheckRun` entry with conclusion `CANCELLED` to the returned `failing` list unconditionally —
with no check for whether a newer run of the *same* check name also appears in the same
`statusCheckRollup` (same head SHA), and no exemption beyond the existing `isRequired`/name-marker
informational carve-out (`_is_informational`, line 260). A CI provider commonly cancels a stale
in-progress run for a check name when a newer run for that same name and commit starts (e.g. a
GitHub Actions `concurrency:` group cancelling a superseded workflow run, or a manual re-run of a
single job) — `gh pr view --json statusCheckRollup` can then return both the old `CANCELLED` entry
and the new run's entry (`SUCCESS`, or still `IN_PROGRESS`/`QUEUED`) for the same name in one
response. `classify_checks()` reports the stale `CANCELLED` entry as a hard failure regardless,
so `_block_on_checks` (`verify.py:1141`), `verify_one`'s check-classification call
(`verify.py:1291`), and `postmerge-reconciliation-audit`'s reuse of the same function
(`audit_postmerge.py:294`) all quarantine or flag a group whose actual, current check state for
that name is passing or still pending — a false-positive failure driven entirely by the
provider's own stale bookkeeping, not by anything the group's diff did.

No test in `tests/orchestrator/` exercises a `CANCELLED` conclusion at all (confirmed via the
existing `classify_checks` coverage in `tests/orchestrator/test_verify.py:827-859`, which only
covers `IN_PROGRESS`/`FAILURE`/legacy `StatusContext` states), and no active change under
`openspec/changes/` names `classify_checks`, `_FAIL_CONCLUSIONS`, or this superseded-run scenario
(checked via `ls openspec/changes`) — this gap has no other candidate covering it.

## What Changes

- `classify_checks()` gains a superseded-run exemption: when a `statusCheckRollup` entry reports
  conclusion `CANCELLED` for a check name, and that same rollup also contains a different entry
  for the identical check name that is not itself `CANCELLED` (settled or still pending), the
  `CANCELLED` entry is treated as stale bookkeeping from a superseded run and is silently
  excluded from both `failing` and `pending` — it neither blocks nor fails the group.
- A `CANCELLED` entry for a check name with no other entry for that same name in the rollup is
  unaffected: it is still classified as a failure exactly as it is today. This preserves the
  existing, correct behavior for a check a human or automation genuinely cancelled with nothing
  superseding it.
- No change to any other conclusion in `_FAIL_CONCLUSIONS`, to the legacy `StatusContext` branch,
  to the `isRequired`/informational-name carve-out, or to the `required`-names pending logic —
  this is scoped to the one false-positive class the brief identifies.

## Capabilities

### Added Capabilities

- `ci-check-classification`: documents `classify_checks()`'s standalone contract (it currently has
  no owning capability spec despite being reused by both `verify.py`'s own gating and
  `postmerge-reconciliation-audit`'s sweep) and adds the superseded-`CANCELLED`-run exemption.

## Impact

- **Code**: `src/worktrail/orchestrator/verify.py` (`classify_checks`).
- **Tests**: `tests/orchestrator/test_verify.py` — a rollup with a `CANCELLED` entry and a
  `SUCCESS` entry for the same check name reports neither pending nor failing for that name; a
  rollup with a `CANCELLED` entry and an `IN_PROGRESS` entry for the same name reports pending,
  not failing, for that name; a rollup with only a `CANCELLED` entry for a name (no superseding
  entry) is unaffected and still reports it as failing.
- **Non-goals**: changing what counts as a failing conclusion for any check that is *not*
  `CANCELLED`; adding new GraphQL fields (e.g. timestamps) to determine which entry is "newer" —
  the exemption only needs to know that a non-`CANCELLED` entry for the same name exists
  somewhere in the same rollup response, not which one is chronologically later; touching
  `postmerge-reconciliation-audit` or any other caller of `classify_checks()` beyond the shared
  function itself (they inherit the fix for free, unmodified).
