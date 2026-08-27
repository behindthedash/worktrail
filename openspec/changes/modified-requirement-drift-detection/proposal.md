## Why

An open OpenSpec change's `specs/<capability>/spec.md` delta carries a `MODIFIED Requirement`
block written against whatever the canonical `openspec/specs/<capability>/spec.md` looked like
at the time. If a sibling change touching the *same* requirement is archived first, archiving
replaces the whole requirement block on the canonical spec with the sibling's version. The still
-open change's delta was never updated to build on that newer version, so when it is eventually
synced or archived, its stale `MODIFIED` block silently overwrites the canonical file again and
reverts the scenarios the sibling change added — with no error, because both deltas independently
look internally valid. This exact sequence happened with `stale-brief-precheck-consolidation
-original-created` (brief `20260822-210611`) and was only caught incidentally by
`openspec validate --all --strict` after the fact. There is currently no proactive check that
catches the divergence before archive is even attempted.

## What Changes

- Add a git-log-based check that flags an open OpenSpec change whose delta declares a `MODIFIED`
  (or `RENAMED Requirement TO`) block for a requirement name that an already-archived change's own
  delta also touched (`ADDED`, `MODIFIED`, or `RENAMED ... TO` the same name) under the same
  capability path, where the archive commit postdates the last commit that touched the open
  change's delta file. This is a local, read-only comparison of on-disk delta headings plus git
  commit timestamps — no model call, matching the existing stale-bookkeeping detection's
  local-only philosophy.
- Surface this as a new dashboard stage/warning in the `worktrail-go` orientation scan
  (`_safe_detect_openspec` in `src/worktrail/router/dashboard.py`), reported alongside — not
  replacing — the existing `stage` value, since drift can be detected regardless of whether the
  change is still mid-implementation, verify-pending, or already `complete`/ready to archive.
- The check only warns; it does not block, auto-resolve, or rewrite any file. Resolution (updating
  the open change's delta to build on the newer canonical content) remains a human/agent step.

## Capabilities

### New Capabilities
- `openspec-delta-drift-detection`: detects and reports when an open OpenSpec change's `MODIFIED`/
  `RENAMED-TO` delta for a requirement has been overtaken by an already-archived sibling change
  that touched the same requirement more recently, before the open change's own archive/sync step
  can silently revert that sibling's newer scenarios.

### Modified Capabilities
(none — this introduces new detection behavior alongside, not a change to, the existing
`openspec-stale-bookkeeping-detection` requirements)

## Impact

- `src/worktrail/router/dashboard.py`: new drift-detection function reusing the existing
  `_OPENSPEC_DELTA_SECTION` / `_OPENSPEC_REQUIREMENT` / `_OPENSPEC_RENAME` parsing helpers, wired
  into `_safe_detect_openspec`'s returned info dict.
- `tests/router/test_dashboard.py`: new coverage using the repo's existing temp-git-repo fixture
  pattern (init a repo, commit an archived change, commit an open change's delta, assert the
  drift signal appears/doesn't appear based on commit ordering).
- No change to the `openspec` CLI, to `worktrail-repo-init`'s CI scaffolding, or to any archive/
  sync workflow — this change is independently buildable and testable now.
