## Why

A run's journal can lose its `groups` map — and its `integrate_complete` marker — partway
through the run, because the orchestrator's own tail phase rewrites the journal with a writer
that does not know about them. The result is a run that quarantined a group but leaves no
record of it: the quarantined work becomes unrecoverable through the documented
`worktrail-resume-group` path, which is exactly the path that exists to clear it.

Observed live 2026-10-03 (run `full-1791066876`, spec `python314-shebang-policy`): the log
printed `!! QUARANTINED [feature-1] task_failure`, but the run's completed journal held no
`groups` key and zero `QUARANTINE` occurrences. `worktrail-resume-group` then failed with
`group 'feature-1': no record in journal` at precisely the point its docstring says it should
clear the record.

The mechanism is a second-writer problem the codebase already knows about.
`_dispatch_pending_tail` calls `live_run_real` a second time with
`out_cassette=journal_path` (`src/worktrail/orchestrator/live.py:5728`). That invocation's
`record()` (`live.py:4916`) builds its journal dict from scratch — `spec_id`, `entries`,
`gitnexus_capability`, plus `run_id`/`budget_stopped_at` when set — and carries nothing else,
so the pipeline phase's `groups` map is destroyed by the wholesale `atomic_write_text`.
`_preserve_plan_pin` (`live.py:564`) exists for this exact failure class and says so in its
docstring, but it only carries `PLAN_PIN_KEYS = ("plan_fingerprint", "plan_fingerprints")`
(`live.py:561`). Once `groups` is dropped it never comes back: the pipeline's own `_record()`
writes it only when non-empty (`live.py:6340-6341`), and
`_mark_integrate_complete_if_terminal` reads terminality from `journal['groups']`
(`integrate.py:717-722`), so `integrate_complete` cannot be re-marked either.

## What Changes

- A run's `groups` map and `integrate_complete` marker SHALL survive any journal rewrite
  performed by a later phase of the same run. The tail phase's journal write carries forward
  the group records the pipeline phase wrote, rather than dropping them.
- A quarantined group record SHALL remain readable by `worktrail-resume-group` after the run
  that quarantined it has completed, so the documented recovery path stays usable.
- Records for groups the tail phase itself resolved SHALL still be updated by that phase —
  this is a carry-forward of state a different writer owns, not a freeze of the whole map.
- Regression coverage: a test that quarantines a group in the pipeline phase and asserts the
  record is still present after the tail phase's rewrite.

## Capabilities

### New Capabilities
<!-- None: this change fixes behavior already governed by an existing capability. -->

### Modified Capabilities
- `quarantined-group-resume`: adds a durability requirement — the group records this
  capability's command operates on must survive the tail phase's journal rewrite, so a
  quarantined group is still clearable after the run that produced it has completed.

## Impact

- `src/worktrail/orchestrator/live.py` — `record()` in `live_run_real` (`:4916`) and the
  journal-preservation helper it calls; `_dispatch_pending_tail`'s call site (`:5728`) is the
  trigger, not a change site.
- `src/worktrail/orchestrator/integrate.py` — `_mark_integrate_complete_if_terminal`'s
  terminality read depends on `groups` surviving; no change expected there.
- Tests: `tests/orchestrator/` gains a regression test proving a quarantined group's record
  survives the tail phase.
- No change to the journal's on-disk format, to `worktrail-resume-group`'s interface, or to
  any other command's behavior.
