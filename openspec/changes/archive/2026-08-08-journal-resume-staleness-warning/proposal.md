## Why

`full_real`'s journal-resume path can silently mask stale state: relaunching
`worktrail-live full-real` without `--fresh` replays a prior run's journal
(quarantine verdicts, review commentary) verbatim when nothing new is
dispatched, exiting `PIPELINE RUN COMPLETE` in ~1s with the same
blocked/quarantined outcome as before — indistinguishable in the log from a
genuine, exhaustive re-attempt. This was hit directly on datalena spec-100
(`run_id full-1784950178`): resuming replayed 0 new agent spawns and
re-quarantined `base`+`feature-1` using cached commentary describing a
Spec-099 FK blocker that had already landed on `dev`, requiring a human/agent
to manually diff the journal's `run_id` against the current brief to notice
nothing actually happened.

## What Changes

- On a `full_real` pipeline resume (`resume=True`, existing journal file
  present), when the reconciled journal contains one or more groups recorded
  `QUARANTINED`, compute how many commits `base` has moved since those
  groups' task branches were originally forked (the same merge-base
  computation `_resume_drift_report` already performs) and, when that count
  is non-zero, print a loud, explicit warning naming the quarantined
  group(s), the drift count, and recommending `--fresh` — distinct from
  `_resume_drift_report`'s existing generic informational line, which prints
  for any resume with drift and does not mention quarantine or `--fresh` at
  all.
- The warning is printed before the run reaches `=== PIPELINE RUN COMPLETE
  ===`, so it is visible in the same output a human/agent would otherwise
  read as "nothing to do here."
- This is a warning, not a hard stop — the operator decides whether to
  re-run with `--fresh` or accept the resumed (possibly stale) outcome. No
  change to `--fresh` behavior, and no change to a resume whose journal has
  no `QUARANTINED` groups.

## Capabilities

### New Capabilities
- `journal-resume-staleness-warning`: on a `full_real` resume whose journal
  contains `QUARANTINED` groups, warns when the base branch has moved since
  those groups' task branches were forked, so a no-op resume of stale
  quarantine state cannot masquerade as a genuine retry.

### Modified Capabilities
(none — no existing `openspec/specs/` capability currently owns
`full_real`'s journal-resume behavior)

## Impact

- `src/worktrail/orchestrator/live.py` — the `full_real`/`_pipeline_scheduler`
  resume path (~line 3378 `if resume and Path(journal_path).exists():`),
  specifically the point where `_resume_drift_report` is already called
  (~line 3400). The journal's `groups` records today carry only
  `{pr_url, head_branch, state}` (`_record_group_fn`) — no quarantine-reason
  text is persisted, so this warning is scoped to what's derivable from
  existing fields: group name + `state == "QUARANTINED"` + branch drift.
  (A structured `quarantine_reason` field, and distinguishing
  budget-exhaustion quarantines from real-failure quarantines, is a
  separate, already-queued concern — brief
  `20260807-135614-worktrail-s-orchestrator-marks-a` — not part of this
  change.)
- No impact on a fresh (`--fresh`) run, or on a resume whose journal has no
  `QUARANTINED` groups.
