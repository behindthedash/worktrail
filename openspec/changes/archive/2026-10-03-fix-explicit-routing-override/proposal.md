## Why

An explicit `--model-map` or `--effort` override can fail after live dispatch has
already selected a valid target. `LiveSpawn` selects from the routing resolved
for the target repository, but `explicit_cell_override()` independently reloads
policy from the process working home; when those policies differ, it cannot find
the selected target and aborts the dispatch.

The routing table selected for a spawn must remain authoritative through the
explicit override path, particularly for repository-local routing.

## What Changes

- Allow `explicit_cell_override()` to construct its temporary one-cell routing
  file from the already-resolved routing table that selected the target.
- Pass the live dispatcher's resolved routing table into that helper for
  explicit model and effort overrides, rather than resolving policy again.
- Preserve the fail-closed error when a caller names a target absent from the
  routing table it supplies.
- Add regression coverage for a repository-local routing table that selects a
  target absent from the machine-wide routing file, plus the existing invalid
  target behavior.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `model-tier-routing`: explicit model and effort overrides retain the routing
  table that selected their execution target.

## Impact

- `src/worktrail/orchestrator/live.py`: supplies its resolved routing table to
  explicit override construction.
- `src/worktrail/orchestrator/spawnlib.py`: builds the temporary explicit cell
  from supplied routing instead of independently reloading policy.
- `tests/orchestrator/`: covers the divergent repo-local and machine-wide
  routing regression and invalid-target validation.
