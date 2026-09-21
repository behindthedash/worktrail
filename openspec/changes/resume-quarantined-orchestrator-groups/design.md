## Context

A group's journal record is the orchestrator's memory of integration. `QUARANTINED` is written
by roughly a dozen paths in `integrate.py`/`live.py` (merge conflict, failing integrated smoke,
add-on failure, pre-PR drift, empty diff, budget exhaustion, dependency quarantine, …) and is a
member of `TERMINAL_GROUP_STATES`, so once written the run calls itself complete. The
per-group resume logic in `live.py:_integrate_verify_group` keys entirely off that record: a
`MERGED` record returns early, a record with a `pr_url` skips integrate and goes to verify —
and a QUARANTINED record is never reached at all, because the resume never re-enters
integration for a run whose journal is already `integrate_complete`.

Recovery today is therefore either manual (fix the branch, `worktrail-land-pr`, hand-run
checkbox-sync and the archive PR) or the sledgehammer `--re-integrate`, which drops *every*
non-MERGED group record and rebuilds the whole run.

## Goals / Non-Goals

- Goals: make one quarantined group re-enter the ordinary integrate/verify resume path with a
  single command; keep the prior quarantine on record; make the action reachable from the
  front door; make the unsafe cases refuse rather than guess.
- Non-Goals: changing `TERMINAL_GROUP_STATES`, `integrate_complete` semantics, or when a group
  is quarantined in the first place; fixing whatever caused the quarantine (a human/agent fixes
  the branch first); automatically retrying quarantined groups inside a run; touching MERGED
  records; any new integrate, smoke, or PR-opening implementation.

## Decisions

- **Clear the record, do not build a second recovery pipeline.** The brief asks for an action
  that re-runs the integrated smoke on the fixed integration branch, opens the group PR, and
  finishes checkbox-sync/archive. All three already exist in `integrate_one` + `verify_one`,
  and a `--resume` whose tasks are all DONE runs a no-op fan-out and goes straight to
  integrate/verify. So the smallest correct change is to remove the one record that makes the
  resume skip the group. A bespoke "recover" pipeline would be a second implementation of
  integration that would drift from the real one.
- **Opt-in per group, never automatic.** Most quarantine reasons mean something really failed.
  Auto-clearing on every resume would re-burn a long integrated smoke run on an unfixed branch
  and could re-open a PR for a group a human deliberately parked. The command names the group.
- **`--all-resumable` is limited to `QUARANTINE_BUDGET_EXHAUSTED`.** `quarantine_selfcheck`
  already documents that category as "the group simply never got a chance to run (it did not
  fail), so it is safely resumable with a plain re-run", and routes it away from human triage.
  Reusing that existing classification avoids inventing a second notion of "safe".
- **Also drop `integrate_complete`.** Leaving it set would leave a journal that claims the run
  finished while a group record is gone; `_mark_integrate_complete_if_terminal` re-stamps it on
  the next resume once every group is terminal again, so clearing it is self-healing rather
  than a state the command has to maintain.
- **Audit entry, not a silent delete.** `resumed_quarantines` keeps the prior
  `quarantine_reason`/`quarantine_detail` so a later triage pass (or a repeat of the same
  failure) can see the group was already recovered once. `_write_group_journal` deliberately
  rebuilds a group record from scratch on every write, so the reason would otherwise be gone
  the moment the group is re-integrated.
- **Refuse while the RunLock is held.** The journal is written by the live run's atomic writer;
  editing it underneath a running orchestrator would be lost at best and inconsistent at worst.
  `journal_selfcheck._runlock_held` already encodes "a live run holds this journal right now"
  (held == live, stale lock files probe free), so the check is a reuse, not new machinery.
- **Do not delete the group's worktree or branch.** The human is expected to have fixed the
  task branch in place; `sweep_stale_worktrees.py` owns worktree teardown and already knows how
  to attribute a worktree to a QUARANTINED group. Touching either here would race that sweep.

## Risks / Trade-offs

- Clearing a group whose branch was *not* actually fixed re-runs a potentially long integrated
  smoke and quarantines it again. Accepted: the outcome is the status quo plus one wasted run,
  and the alternative (a precondition check that the failure is fixed) would mean reproducing
  the smoke run inside the command.
- A group whose PR was already opened before quarantine has a `pr_url` in its record. Clearing
  the record loses that pointer for the resume, which will open a fresh PR. The audit entry
  keeps the old URL visible so a human can close the duplicate; detecting and reusing an open
  PR is `integrate_one`'s existing concern, not this command's.

## Migration Plan

Additive: a new console script plus output/doc wording. Journals written before this change are
read unchanged; `resumed_quarantines` is absent until the command runs.

## Open Questions

None.
