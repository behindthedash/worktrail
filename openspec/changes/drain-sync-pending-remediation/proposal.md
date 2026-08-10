## Why

`dashboard.py`'s `detect_stage()` already computes a `sync-pending` stage
(UI-labeled "Needs sync") for devkit specs that are code-complete and only
need `/opsx:sync` to reconcile the spec against merged code and refresh
`knowledge-graph.json`. Like `verify-pending` and `stale-bookkeeping` before
it, this stage is invisible to `worktrail-go auto` (auto mode only claims
work-queue briefs, never dashboard stages), so a sync-pending spec sits
unresolved until a human happens to notice it on the dashboard.
`REMEDIATION_TABLE` in `drain.py` already exists precisely to close this gap
for the other two stall-recoverable stages; sync-pending is the next
one-line entry.

## What Changes

- Add a `find_sync_pending_specs` finder to `drain.py`, mirroring
  `find_verify_pending_specs`: scan `dashboard.scan()` rows per repo under
  `--repos-root` (or `--go-repo`), keep rows with `stage == "sync-pending"`,
  resolve each spec's path with the existing `resolve_spec_rel`.
- Add a `resume_sync_pending` action that spawns a headless one-shot agent
  CLI running `/opsx:sync <spec_id>` for the finding (mirroring the
  `claude -p` / `codex exec` / `opencode run` per-agent command shapes
  `build_command` already uses for the main drain loop), via the same
  `spawner`/`SpawnOutcome` contract the two `_resume_via_full_real` rows use.
- Add one new `StageRemediation` row — `sync_pending` — to
  `REMEDIATION_TABLE`, pairing the new finder and action. No changes to
  `sweep_remediations`'s engine itself: the existing generic iterate +
  per-finding try/except loop picks up the new row automatically.
- Extend `drain()`'s returned summary dict with a `resumed_sync_pending` key
  (same list-of-result-dict shape as `resumed_verify_pending` and
  `resumed_stale_bookkeeping`), so the fourth table row is visible in the
  summary the same way the third one was when it was added.

## Capabilities

### Modified Capabilities
- `drain-stage-remediation-table`: adds a fourth safe, unattended-recoverable
  remediation category (`sync-pending`) to `REMEDIATION_TABLE`, and extends
  the backward-compatible summary dict requirement to cover the new
  `resumed_sync_pending` key.

## Impact

- `src/worktrail/drain/drain.py`: new finder function, new action function,
  one new `REMEDIATION_TABLE` entry, one new summary dict key.
- `tests/` (mirrors `src/worktrail/drain/drain.py`): new coverage for the
  finder, the action's spawned command shape, the table entry's
  finder→action wiring through `sweep_remediations`, and the summary dict's
  new key.
- No changes to `dashboard.py` (the `sync-pending` stage already exists) or
  to `sweep_remediations`'s engine.
