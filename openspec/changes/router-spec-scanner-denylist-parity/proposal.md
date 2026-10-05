## Why

Several router helpers enumerate direct children of a devkit `docs/specs/` root independently. Unlike the dashboard selfcheck, they do not consistently exclude the dashboard's known shared, non-spec directories, so loose Markdown in a directory such as `addenda/` can be treated as a requirement-coverage target or a handoff candidate.

The dashboard already owns the authoritative name-based denylist. Bringing the remaining devkit-root scanners into parity prevents false positives without hiding legitimate, incomplete spec folders.

## What Changes

- Make the devkit `docs/specs/` enumeration used by requirement-coverage auditing, handoff classification, and overlap extraction honor `dashboard._NON_SPEC_DIRS`.
- Reuse the shared denylist rather than copying its entries or using the dashboard's content-based `_is_spec_folder()` predicate.
- Add focused regressions showing denylisted directories with otherwise scanner-shaped Markdown are ignored, while a non-denylisted directory remains eligible for each scanner's existing behavior.
- Preserve OpenSpec root discovery and all existing parsing, scoring, coverage, and stage semantics outside the direct-child denylist boundary.

## Capabilities

### New Capabilities
- `router-spec-scanner-denylist-parity`: consistent name-based exclusion of known non-spec directories across router scanners of devkit spec roots.

### Modified Capabilities
None.

## Impact

- `src/worktrail/router/check_req_coverage.py`, `classify_handoff.py`, and `overlap_check.py`.
- Their corresponding router test modules.
- No CLI arguments, external dependencies, persisted data, or changes to OpenSpec container scanning.
