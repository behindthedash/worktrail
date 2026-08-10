## Context

`dashboard.scan(repo/docs/specs)` intentionally returns both devkit rows and
active `openspec/changes/*` rows. PR #296's `find_sync_pending_specs()` already
uses that common scan and `resolve_spec_rel()` already resolves either format.
The format gap is therefore entirely in `_safe_detect_openspec()`: after tasks
and verification complete it unconditionally returns `complete` with next action
`sync/archive`.

OpenSpec sync is an agent-driven merge, so byte-for-byte file equality is not a
valid signal. A delta is reconciled when its observable declarations are present
in the canonical capability spec: ADDED/MODIFIED requirement names and scenario
names are present, REMOVED requirement names are absent, and RENAMED FROM names
are absent while TO names are present.

## Goals / Non-Goals

### Goals

- Give unsynced, task-complete OpenSpec changes a deterministic `sync-pending`
  stage that the existing drain row can consume.
- Make a successful `/opsx:sync` disappear from the next sweep without adding a
  mutable marker or requiring the OpenSpec CLI during dashboard scans.
- Preserve `verify-pending` precedence and per-change error isolation.

### Non-Goals

- Do not change the existing sync action, command shape, remediation table, or
  summary dict.
- Do not archive changes automatically; archive remains a separate lifecycle
  decision after canonical specs are reconciled.
- Do not attempt semantic equivalence of requirement prose. The detector uses
  stable headings as a conservative workflow signal.

## Decisions

### D1: Detect reconciliation from requirement and scenario headings

Add a private parser that reads each `specs/**/spec.md` delta and its matching
`openspec/specs/<capability>/spec.md`. It evaluates headings under ADDED,
MODIFIED, REMOVED, and RENAMED sections. ADDED/MODIFIED requirements must exist
in the canonical spec; any scenarios declared by the delta must also exist.
REMOVED requirements must not exist. RENAMED pairs require FROM absent and TO
present. Missing canonical files are unsynced. A change with no delta specs is
not sync-pending.

This is deliberately conservative: it proves the structural result that the
idempotent sync skill itself produces without reproducing the skill's prose
merge judgment.

### D2: Insert sync-pending after verify-pending

`_safe_detect_openspec()` keeps its current ordering: pending tasks first,
then open/unmerged group verification. Only the final branch checks delta
reconciliation. Unsynced becomes `sync-pending`; reconciled (or no deltas)
remains `complete` with archive as its next action.

### D3: Reuse the existing drain machinery unchanged

`find_sync_pending_specs()` already calls the mixed-format dashboard scan and
resolves `openspec/changes/<id>`. Once the dashboard emits the stage, the
existing `worktrail-skill-dispatch --skill opsx:sync` action is sufficient.

## Risks / Trade-offs

- Heading-only comparison can miss a prose-only modification whose requirement
  and scenario names are unchanged. This is acceptable for the first narrow
  contract: such a delta has no deterministic, CLI-free proof of reconciliation.
  Tests document the boundary and the detector fails toward leaving the change
  visible when a declared structural element is missing.
- A malformed delta degrades through `_safe_detect_openspec()`'s existing error
  row rather than silently claiming completion.

## Test Strategy

- Unsynced ADDED/MODIFIED/REMOVED/RENAMED declarations report sync-pending.
- Reconciled declarations report complete/archive.
- Verify-pending outranks sync-pending.
- The existing drain finder returns an OpenSpec path for the new stage and
  stops returning it after reconciliation.

