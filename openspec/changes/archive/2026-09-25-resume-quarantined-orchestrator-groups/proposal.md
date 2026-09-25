## Why

`TERMINAL_GROUP_STATES = {"OPEN", "MERGED", "QUARANTINED"}`
(`src/worktrail/orchestrator/integrate.py:44`) is read by
`_mark_integrate_complete_if_terminal` (`integrate.py:698`, filter at line 720), so a run with a
QUARANTINED group still stamps `integrate_complete: true`, dispatches the tail, and reports done.
A resume then has nothing to re-enter: the group's journal record permanently says QUARANTINED,
and the only way back is `--re-integrate`, which clears **every** non-MERGED group record
(`live.py:_clear_integration_state`) and re-integrates the whole run — too blunt to reach for when
one group failed on a one-line fix.

The result is that a group quarantined on a trivially fixable smoke failure needs a fully manual
recovery. Run `full-1789943412` (spec `land-pr-wait-for-required-check-contexts`, 2026-09-20)
quarantined `feature-1` after a 13-minute integrated smoke run failed a single check (EXE001: a
new test file carried a shebang with mode `100644`); recovery took a hand-fixed task branch, a
manual `worktrail-land-pr`, and a manual checkbox-sync + archive PR (#1306, #1308 — both on `main`
in this checkout). The dashboard currently lists 59 quarantined groups, the oldest ~56 days old.

`quarantine_selfcheck.py` already *classifies* quarantines (`resumable` for
`QUARANTINE_BUDGET_EXHAUSTED`, `reconciled` for landed-out-of-band, `findings` for the rest) and
`sweep_stale_worktrees.py` cleans their worktrees up — but nothing puts a quarantined group back
into the pipeline. Both prior quarantine changes are archived and detection/teardown only
(`2026-08-13-quarantine-reconciliation`, `2026-09-11-quarantined-group-retroactive-worktree-cleanup`).
(Work-queue brief `20260920-165627-resume-quarantined-orchestrator-groups`.)

## What Changes

- New console script `worktrail-resume-group` (`src/worktrail/orchestrator/resume_group.py`): for
  one named group in one run journal, drop that group's QUARANTINED record and the journal's
  `integrate_complete` marker, leaving every other group record untouched. The next
  `worktrail-live full-real --resume` then re-integrates exactly that group through the normal
  path — re-running the integrated smoke on the (fixed) task branches, opening the group PR, and
  running the existing checkbox-sync/verify machinery — because the run's tasks are already DONE,
  so the fan-out is a no-op.
- The prior quarantine is never silently discarded: each clear appends a `resumed_quarantines`
  audit entry (group, prior `quarantine_reason`/`quarantine_detail`, `cleared_at`) to the journal.
- Safety: the command refuses when a live run holds the journal's RunLock, and refuses to clear a
  group that is not in state `QUARANTINED`. `--dry-run` reports what would be cleared and writes
  nothing.
- `--all-resumable` clears every group whose `quarantine_reason` is `QUARANTINE_BUDGET_EXHAUSTED`
  — the category `quarantine_selfcheck` already documents as "never failed, safely resumable".
  Any other reason must be named explicitly with `--group`, so a real failure is a deliberate call.
- The command prints the exact `worktrail-live full-real --resume` invocation to run next, and
  `worktrail-quarantine-selfcheck`'s output plus Route E in `skills/worktrail-go` point at it, so
  the recovery path is reachable from the front door instead of being reconstructed by hand.
- `TERMINAL_GROUP_STATES` is unchanged: QUARANTINED still means "not in flight". Nothing about a
  run that is not explicitly targeted by this command changes.

## Capabilities

### New Capabilities
- `quarantined-group-resume`: an explicit, audited, single-group un-quarantine action that makes a
  QUARANTINED group re-enter the ordinary integrate/verify resume path.

### Modified Capabilities

## Impact

- `src/worktrail/orchestrator/resume_group.py` (new) + `pyproject.toml` `[project.scripts]` entry.
- `src/worktrail/router/quarantine_selfcheck.py`: findings/resumable output names the command.
- `skills/worktrail-go/references/routes.md`: Route E step 4 cites the command for quarantined
  orchestrator groups.
- `tests/orchestrator/test_resume_group.py`, `tests/router/test_quarantine_selfcheck.py`.
- No change to orchestrator run semantics, journal schema for existing keys, or task formats.
