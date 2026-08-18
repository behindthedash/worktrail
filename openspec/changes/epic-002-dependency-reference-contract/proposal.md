## Why

Epic 002's business objective is to prevent malformed work-queue dependency references from silently bypassing sequencing, so operators can trust that automatic pickup respects active prerequisites. The supported handoff creation surface currently accepts comma-joined `blocked-by` values and can therefore write ambiguous dependency metadata into the queue instead of rejecting it at the producer boundary.

## What Changes

- Define the accepted contract for each individual `blocked-by` value supplied to handoff creation.
- Validate `blocked_by` in the `create_handoff()` Python API before creating a queue directory or brief.
- Reject comma-joined values with an actionable error directing callers to repeat `--blocked-by` once per dependency.
- Preserve valid repeated `--blocked-by` CLI arguments as distinct list entries in brief frontmatter.
- Add focused API and CLI regression tests covering accepted identifiers, invalid comma-joined and blank/whitespace values, actionable failure text, and no partially written brief.
- Keep this feature limited to producer validation; runtime resolution, conservative handling, diagnostics, and normalization of already-malformed references remain later Epic 002 work.

## Capabilities

### New Capabilities

- `work-queue-dependency-reference-contract`: Defines the single-reference contract enforced by the supported work-queue handoff creation API and CLI.

### Modified Capabilities

None.

## Impact

- Affected implementation: `src/worktrail/workqueue/create_handoff.py`.
- Affected tests: `tests/workqueue/test_create_handoff.py`.
- API/CLI compatibility: malformed `blocked_by` inputs that were previously serialized are rejected; valid repeated CLI arguments remain supported and retain list ordering.
- Security/data: fail-fast validation prevents ambiguous dependency metadata from entering trusted queue storage and avoids writing a partial brief on rejection; it does not rewrite or reinterpret existing queue data.
- UX: failures identify the invalid value and show the repeated-flag form needed to express multiple dependencies.
- Owning plan: `docs/specs/epics/002-safe-work-queue-dependency-references.md`, Feature 1, in service of its business objective that malformed references must not silently bypass sequencing.
