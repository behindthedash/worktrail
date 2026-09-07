## Why

Epic 002 Feature 2 made malformed and ambiguous `blocked-by` references fail closed inside `work_queue.py`, but the resulting classification stays private: a brief blocked by a comma-joined legacy value now reports a bare `blocked: true`, indistinguishable from one waiting on a genuinely active prerequisite. An operator therefore cannot tell from `worktrail-work-queue list`, the dashboard, or an auto-pick miss log which brief carries corrupt frontmatter or what the invalid value is, and the highest-risk consumer of the fix — automatic selection — has no direct regression evidence that the 2026-08-18 incident shape stays skipped.

## What Changes

- Expose the structured dependency classification already computed for each queued brief through `worktrail-work-queue list --json`, as an additive per-brief field carrying the raw value, normalized reference, state, and matching candidates for every unsatisfied reference.
- Give automatic selection a stable, structured skip reason that distinguishes a malformed or ambiguous dependency reference from ordinary blocking, while preserving the existing coarse `blocked` bucket used by the auto-pick miss log.
- Add an actionable operator warning to human-readable queue listing and the rendered dashboard that names the affected brief and the raw invalid dependency value, so corruption can be located and repaired without inspecting YAML or reading logs.
- Add end-to-end regression coverage proving that a brief whose `blocked-by` holds a comma-joined value — with the first embedded dependency still active — is skipped by automatic selection with a dependency-specific reason rather than ranked as maximally eligible.

## Capabilities

### New Capabilities

- `work-queue-dependency-diagnostics`: Defines how unresolved dependency-reference classifications are surfaced to queue-list JSON, automatic selection skip reasons, and human-readable operator output, and the end-to-end guarantee that malformed references cannot reach an automatic pick.

### Modified Capabilities

None.

## Impact

- Adds an additive per-brief diagnostics field to `list_queue()` in `src/worktrail/workqueue/work_queue.py` and an operator warning to its `list` human output.
- Refines the auto-pick skip reason and rendered queue tag in `src/worktrail/router/dashboard.py`, which already receives queue briefs verbatim as `--queue-json`.
- Updates the documented auto-mode skip-reason vocabulary in `skills/worktrail-go/references/auto-mode.md`.
- Adds JSON-contract, rendering, and end-to-end auto-selection coverage in `tests/workqueue/test_work_queue.py` and `tests/router/test_dashboard.py`.
- Does not mutate queue briefs, change producer input validation (Epic 002 Feature 1), or alter the classification rules themselves (Epic 002 Feature 2).
