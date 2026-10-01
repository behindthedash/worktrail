## Context

`LiveSpawn` resolves and selects an execution cell from `self._routing` before
applying a run-level effort or role-level model override. The override helper
then reloads routing policy from `worktrail_home()`, which can resolve a
different machine-wide table from the repository-local table that selected the
cell. See proposal.md for the incident evidence.

## Goals / Non-Goals

**Goals:**

- Keep an explicit override bound to the exact resolved routing table that
  selected its target.
- Preserve the temporary one-cell routing-file mechanism and its fail-closed
  validation of target declarations.

**Non-Goals:**

- Change normal tier selection, fallback order, or repository-policy
  precedence.
- Change the temporary routing file's lifetime or make it persist on disk.
- Add a new routing configuration key or environment-variable override.

## Decisions

- **Pass resolved routing into the override helper.** `LiveSpawn` already owns
  the authoritative routing table and the selected cell. Supplying that table
  to `explicit_cell_override()` makes target lookup use the same policy
  snapshot, rather than attempting to reproduce repository context inside the
  helper. Reloading policy was rejected because it is the source of the
  divergent-target failure.

- **Keep target validation in the helper.** The helper continues to reject a
  target not declared by its supplied table before it writes the temporary
  routing file. This retains a direct, testable safety boundary for other
  callers instead of trusting every caller to validate beforehand.

- **Test both the helper and live integration boundary.** A helper test proves
  the supplied table controls validation; a live-spawn regression uses
  divergent repository-local and machine-wide routing to prove the caller
  passes its selected table. Testing only the generated YAML would not catch a
  future caller omission.

## Risks / Trade-offs

- [A caller supplies stale or malformed resolved routing] → the helper retains
  its target-existence check and the existing routing resolver remains
  responsible for producing valid tables.
- [Other callers still rely on policy reloading] → retain a deliberate API
  decision in the helper's tests and update every explicit-override caller in
  this focused change.

## Migration Plan

No configuration or data migration is required. The fix is effective for new
live spawns; rollback is a code revert with no persistent state to clean up.
