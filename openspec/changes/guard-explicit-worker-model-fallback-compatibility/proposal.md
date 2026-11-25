## Why

`LiveSpawn.__call__` selects the first available cell in a role's fallback tier and then applies
`--model-map` through a temporary one-cell routing override. If fallback changes the serving
target's harness, the explicit model can belong only to the original harness; the current path
still launches that incompatible pair. The recent thread-local override fix isolates concurrent
overrides but does not validate this compatibility boundary.

## What Changes

- Validate an explicit worker model against the harness of the cell that actually won fallback
  selection before creating the temporary override or launching a worker.
- Treat models declared by the resolved routing table for targets using that harness as its
  supported model set, so the check remains configuration-driven and supports multiple targets
  per harness.
- Fail loudly and actionably when the requested model is unsupported by the winning harness;
  retain existing behavior for compatible explicit models, effort-only overrides, and automatic
  routing without an explicit model.

## Capabilities

### New Capabilities

### Modified Capabilities

- `model-tier-routing`: explicit model overrides must remain compatible with the fallback cell's
  serving harness.

## Impact

- `src/worktrail/orchestrator/live.py`: validate a role-model override after authoritative cell
  selection and before the explicit-cell override is entered.
- `src/worktrail/orchestrator/spawnlib.py`: provide the routing-derived compatibility predicate
  or error boundary used by live spawning.
- `tests/orchestrator/test_live_extras.py` and `tests/orchestrator/test_spawnlib.py`: cover a
  cross-harness fallback rejection and compatible override preservation.
- No new dependencies, CLI flags, routing-file schema, or external model lookup.
