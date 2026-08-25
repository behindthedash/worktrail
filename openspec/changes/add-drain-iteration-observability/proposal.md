## Why

Nightly drain summaries currently preserve an iteration's aggregate outcome and inferred brief label but omit the attribution and failure evidence used to derive them. The 2026-08-25 run therefore could not explain two `blocked` iterations reported with no brief or diagnose why brief `20260717-093000-datalena-customerx-mvp-phase-m-hardening` failed without reconstructing the run from ephemeral context.

## What Changes

- Add structured per-iteration summary fields for the classified failure, queue claim delta, claimed-brief attribution count, and a durable transcript or equivalent diagnostic pointer.
- Include the same key attribution context in the human-readable per-iteration drain log so blocked and failed outcomes do not collapse to an unexplained aggregate label.
- Add regression coverage for blocked iterations with no attributed brief and failed claimed-brief iterations whose summary must remain independently diagnosable.
- Keep the change confined to drain summary/log production and its tests; digest rendering and unrelated drain behavior remain unchanged.

## Capabilities

### New Capabilities

- `drain-iteration-observability`: Durable, structured diagnostic context for each nightly drain iteration.

### Modified Capabilities

None.

## Impact

The change affects `src/worktrail/drain/drain.py`'s per-iteration log line and JSON-compatible summary entries plus focused tests in `tests/drain/test_drain.py`. Consumers of the summary gain additive fields; no existing fields, stop semantics, command-line behavior, or dependencies change.
