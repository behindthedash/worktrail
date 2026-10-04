## Why

A completed `worktrail-live full-real` run can leave a journal whose `groups` map is missing
every record the pipeline phase wrote, so a group the run quarantined is unreadable by
`worktrail-resume-group` -- the one command that exists to clear it. The tail phase's wholesale
rewrite was already fixed
(`openspec/changes/archive/2026-10-03-tail-rewrite-preserves-group-records`: `PIPELINE_PHASE_KEYS`
+ `_carry_forward_keys`), and that fix holds for the tail write. The records still do not
survive the run. A **second, unidentified writer** drops them before the run ends.

Confirmed on the journal the source brief cites
(`20261003-224323-second-writer-drops-group-records`),
`$HOME/projects/worktrail-worktrees/quarantined-group-resume-chg-tail-rewrite-preserves-group-records-worktrees/run-tail-rewrite-preserves-group-records.json`
(mtime 2026-10-03 22:37; `run_id: full-1791087959`, spec
`tail-rewrite-preserves-group-records`): it ends with
`groups = {"checkbox-sync": {state: MERGED, pr_url: .../pull/1419}, "tail-2.1": {state: MERGED}}`
and **no `integrate_complete` key at all**. Mid-tail the same run's journal carried
`groups = {"feature-1": {state: MERGED, pr_url: .../pull/1415}}` *together with*
`integrate_complete: true` -- the tail fix working -- so the pipeline phase's record was on disk
and was dropped afterwards. The end state is not an artifact of that fix: the pre-fix run the
original brief cites, `$HOME/projects/worktrail-worktrees/run-python314-shebang-policy.json`
(mtime 2026-10-03 18:22), ends with `groups = {"checkbox-sync": MERGED}` and no
`integrate_complete` either, despite its log having printed
`!! QUARANTINED [feature-1] task_failure`.

**The root cause is unidentified**, and this change is written to attribute it rather than to
guess at it. Every writer reached by `full-real` was inspected in this checkout; only two
rebuild the journal dict from scratch, and everything else is a read-modify-write that
preserves keys it does not own:

| writer | shape |
|---|---|
| `live_run_real.record()` (`live.py:4947`) | wholesale rebuild; carries `PLAN_PIN_KEYS + PIPELINE_PHASE_KEYS` (`live.py:4963`) -- the tail fix |
| `_pipeline_scheduler._record()` (`live.py:6370`) | wholesale rebuild; writes **only its in-memory `groups_journal`**, and only when non-empty (`live.py:6378`), seeded from disk only under `resume` (`live.py:6333`) |
| `_write_group_journal` (`integrate.py:633`), `_mark_integrate_complete_if_terminal` (`integrate.py:698`), `_record_unreconciled_tail_evidence`, `_record_checkbox_status_divergence`, `_record_plan_fingerprint`, `progress.append_safety_net_events`, `clear_tasks`/`skip_tasks` | read-modify-write; preserve `groups` and `integrate_complete` |
| `progress.set_phase` | sidecar `.status.json` only |

So the drop is either a `_pipeline_scheduler` re-entry whose `groups_journal` was never seeded
from disk (`_record()` would then write a partial `groups` and drop `integrate_complete`
entirely -- the observed shape exactly) or a wholesale writer not yet traced. The end state's
two surviving records are both written *after* the tail by read-modify-write paths
(`checkbox-sync` by `integrate.sync_checkbox_status`; `tail-2.1` by
`reconcile_unreconciled_tail_evidence`'s synthetic `tail-<task-id>` group, `integrate.py:2204`),
which is consistent with an intermediate wholesale write having already dropped the pipeline
phase's record before those landed.

The user-visible impact is exactly what the merged requirement
(`openspec/specs/quarantined-group-resume/spec.md`, "A run's group records survive a later
phase's journal rewrite") exists to prevent, and it is reachable end to end: the run's final
journal does not carry the quarantine record the run produced, and the recovery path the brief
names cannot see it.

## What Changes

- **Attribute the writer before fixing it.** The change carries an attribution task, not a
  guessed fix: reproduce the loss end to end, instrument the run-journal write path so each
  write records its writer and the `groups`/`integrate_complete` key set before and after, and
  bisect against the run's own log timestamps to name the writer. The candidates above are the
  hypothesis set, not the answer.
- **Carry the pipeline phase's records forward at whichever writer the attribution names.** The
  mechanism stays the existing declared-key carry-forward (`_carry_forward_keys` plus
  `PIPELINE_PHASE_KEYS`), extended to the writer found -- specifically to a writer that rebuilds
  a *partially* populated `groups` map, which a pure absent-key carry-forward cannot repair: the
  on-disk records this invocation did not itself write are merged in before the write, and
  `integrate_complete` is carried with them.
- **Make the class of bug fail CI instead of recurring.** The tail rewrite and this one are the
  second and third occurrences of the same second-writer defect, and the codebase already has
  the precedent for policing it statically
  (`tests/orchestrator/test_quarantine_write_sites_structural.py`): a new AST guard enumerates
  every journal-rebuild write site and fails when one does not carry the declared cross-phase
  keys.
- **Prove it end to end, on the run's *final* journal.** The regression the brief specifies:
  quarantine a pipeline group in a throwaway repo, run the whole `full-real` sequence with an
  injected fake spawn and verifier, and assert the record survives to the journal the run leaves
  behind. An intermediate snapshot is not evidence -- the cited run's own 22:23 snapshot looked
  correct while the 22:37 end state did not.
- **Non-goals:** reconstructing records already lost in journals in the field; making
  `worktrail-resume-group` or any other reader defensive (the records are either there or the
  writer is fixed); unifying all journal writes behind one writer, one lock, or one process;
  changing the journal's on-disk format or key names; the separate
  `quarantine-recovery-command` work.

## Capabilities

### New Capabilities

<!-- None: this change fixes behavior already governed by an existing capability. -->

### Modified Capabilities

- `quarantined-group-resume`: the record-durability requirement is strengthened from "any
  journal write performed by a later phase" to the whole run -- every later phase, every later
  invocation sharing the journal, and the run's final write -- and it now states the obligation
  a wholesale rebuild owes: carry the records it did not itself write, explicitly. The
  capability's command contract is unchanged.

## Impact

- `src/worktrail/orchestrator/live.py` -- the wholesale write the attribution names (the
  `_pipeline_scheduler._record()` / `live_run_real.record()` / `_carry_forward_keys` family) and
  the instrumentation used to find it.
- `src/worktrail/orchestrator/integrate.py` and `src/worktrail/orchestrator/progress.py` -- only
  if the attribution lands the writer or a shared write helper there.
- `tests/orchestrator/` -- the end-to-end regression (a `full-real` sequence against a throwaway
  repo with a quarantined pipeline group) and the AST guard over the journal-rebuild write sites.
- No new console script, policy key, dependency, or journal schema; `worktrail-resume-group`,
  `worktrail-live`, and every group record's on-disk shape are unchanged.
