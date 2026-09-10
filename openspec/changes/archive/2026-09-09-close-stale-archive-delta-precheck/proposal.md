## Why

`worktrail-close-stale-openspec` (`src/worktrail/router/close_stale_openspec.py`,
`flip_and_archive`) goes straight from flipping `tasks.md` checkboxes to
`openspec archive -y <id> --json`. Nothing between those two steps compares the
change's delta specs under `openspec/changes/<id>/specs/` against the canonical
`openspec/specs/` tree, and nothing runs `openspec validate --strict` first.
`openspec archive` applies the deltas as part of archiving, so a delta that has
drifted from the canonical spec -- a `MODIFIED`/`REMOVED`/`RENAMED` requirement
whose name no longer exists in `openspec/specs/<capability>/spec.md`, or a delta
overtaken by an archived sibling that touched the same requirement more recently
-- is discovered only when `openspec archive` itself fails (or, worse, succeeds
and silently reverts the sibling's newer scenarios onto the canonical spec).

By that point the checkboxes have already been flipped in the fix-branch
worktree, so the dispatching agent is left with a half-mutated worktree, an
opaque archive error string, and no structured indication of which requirement
is the problem. The dashboard already computes exactly this drift
(`_openspec_delta_drift` and `_openspec_delta_reconciled` in `dashboard.py`,
backed by the `openspec-delta-drift-detection` capability) but reports it only
as an orientation-time warning; the close-stale command never consults it.

PR #1097 (`018a78da`) fixed the parent brief's primary defect (a change whose
tasks were already fully checked refused to archive) and does not add any
pre-check, so this gap is still open. The `worktrail-go` close-stale dispatch
row today says only that the command "flips the checkboxes, runs
`openspec archive -y --json`" -- it does not instruct the agent to run
`openspec validate --strict` either, so there is no manual step covering this
gap.

## What Changes

- Add a delta pre-check to `flip_and_archive` that runs **before any checkbox
  is flipped**: (1) `openspec validate <id> --strict` in the worktree, and
  (2) a structural comparison of every delta file under the change's `specs/`
  against the canonical `openspec/specs/` tree, refusing when a
  `MODIFIED`/`REMOVED`/`RENAMED FROM` requirement is missing from the canonical
  spec, or when the dashboard's existing drift check reports an archived sibling
  overtaking the change's delta.
- Surface the pre-check outcome as a structured `precheck` field in the JSON
  result (validate output, missing canonical targets, drift findings) so the
  dispatching agent sees which requirement/capability is the problem instead of
  an archive error string.
- Add a `--allow-delta-drift` flag for the archived-sibling drift class only
  (never for validate failure or missing canonical targets), so an agent that
  has already reconciled the delta in the uncommitted worktree can proceed.
- Update the `worktrail-go` close-stale dispatch row to describe the pre-check
  and the refusal path.

## Capabilities

### New Capabilities

- `close-stale-archive-delta-precheck`: the close-stale command refuses, before
  mutating anything, to archive a change whose delta specs would fail
  `openspec validate --strict`, target requirements absent from the canonical
  spec, or have been overtaken by an archived sibling.

### Modified Capabilities

(none -- `openspec-delta-drift-detection` specifies the dashboard-side check,
whose behavior is unchanged; this change consumes it.)

## Impact

- `src/worktrail/router/close_stale_openspec.py` -- pre-check, result shape,
  new CLI flag.
- `tests/router/test_close_stale_openspec.py` -- hermetic coverage of every
  refusal class, the ordering guarantee (no flips on refusal), and the override.
- `skills/worktrail-go/SKILL.md` -- close-stale dispatch row.
- `tests/test_plugin_surface.py` -- pins the skill row's pre-check wording.

Out of scope: changing `drain.py`'s `archive_openspec_change` (a different,
non-reusable shape that assumes checkboxes are already flipped), changing the
dashboard's drift detection, or auto-repairing a drifted delta.
