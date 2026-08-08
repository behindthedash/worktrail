## Why

`run_record.py`'s `active-conflicts` scan (and the `claim`/`sibling-worktree-check`
guards built on it) treats every non-terminal run record targeting a `specification`
as a live conflict, with no way to tell a genuinely active run from an orphaned one.
A run whose worktree was deleted and whose work already merged to base still blocks
new work on the same `spec_id` indefinitely — reproduced live: `go-20260717-085119`
(datalena) sat `status=executing` for three weeks after its change merged, silently
blocking a new `053` change until it was manually diagnosed and hand-closed via
`finish`. This affects every repo using worktrail-go's Route C/D/F/G worktree setup
and the `implement` pipeline's active-conflicts scan, not just datalena. Doing this
diagnosis by hand every time it recurs does not scale.

## What Changes

- Add a staleness check to the active-conflicts scan: a non-terminal run record is
  reclassified as **stale** (not a live conflict) when both hold: its `worktree` path
  no longer exists on disk, AND its `files_changed` paths are present in the base
  branch's tree (i.e. the work already landed).
- `active-conflicts` (the read-only scan) partitions its result into live conflicts
  and stale-reconcilable records instead of returning one flat list. Callers that
  hard-stop on any non-empty result (`#active-conflicts-scan`) hard-stop only on the
  live partition; a stale-only result is reported for visibility but does not block.
- Add a `reconcile` subcommand to `run_record.py` that closes a specific stale run
  record (`finish --status completed_and_merged`, recording that it was closed by
  the staleness check rather than by its own session) — the automatic remediation
  path, so a future hit doesn't need a human to re-derive the same diagnosis.
- `#active-conflicts-scan` (subagent-prompts.md) gains a call to the staleness
  partition before its hard-stop check, and reconciles/logs any stale record it
  finds instead of blocking on it.

## Capabilities

### New Capabilities
- `active-conflicts-staleness-reconciliation`: classification of a non-terminal run
  record as stale (worktree gone + files already merged to base) versus a live
  conflict, and the automated `reconcile` path that closes a confirmed-stale record
  without human intervention.

### Modified Capabilities
(none — no existing `openspec/specs/` capability documents the active-conflicts scan's
current behavior; `implement-pipeline-active-conflicts-guard`'s change directory only
edited skill markdown and has not been synced/archived into `openspec/specs/`)

## Impact

- `src/worktrail/router/run_record.py` — `_active_conflicts`, `cmd_active_conflicts`,
  new staleness-check helper, new `cmd_reconcile`/`reconcile` subcommand.
- `run_record.py`'s own module docstring and `active-conflicts` subcommand help text
  (its one existing doc reference, `contracts/active-conflicts-cli.md`, does not
  actually exist in this repo — update the docstring's JSON-shape description
  in place rather than authoring that file).
- `skills/worktrail-go/references/subagent-prompts.md` — `#active-conflicts-scan`
  updated to consume the partitioned result and reconcile stale entries before its
  hard-stop check.
- No change to `claim`'s existing `_lock_is_stale` (record-gone-or-terminal) logic —
  it is a narrower, separate staleness definition for the lock file itself and is
  out of scope here.
- Out of scope: cross-machine reconciliation (a stale record whose worktree lived on
  a different machine this machine cannot inspect) — left for a follow-up if it
  proves necessary in practice.
