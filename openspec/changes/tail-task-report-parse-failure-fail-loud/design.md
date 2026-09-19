## Context

`_dispatch_pending_tail` runs the tail pass by calling `live_run_real` a second time and
returns its result dict. `_pipeline_scheduler` uses that dict only as a fresher task list for
checkbox sync and unreconciled-evidence detection -- it never inspects task status. Every
other failure mode in the pipeline is at least visible in the journal; a tail-task failure is
the one that produces a clean banner and rc=0, which is what made the incident invisible.

## Goals / Non-Goals

- Goals: a failed tail task is recorded, named in the final banner, and reflected in the
  process exit code.
- Non-Goals: retrying the worker on an unparseable report (the run has already spent its
  budget; a resume re-drives the non-terminal task); changing `salvage_report`; changing how
  non-tail task failures are reported (they surface through quarantine and group state
  already); exit codes for `live` or other subcommands.

## Decisions

- **Detect from status, not from the parse site.** The three `report parse FAILED` sites are
  role-loop internals in two different schedulers. Gating loudness on one of them would miss
  a tail task that failed for another reason (timeout, crash, escalation) with the same
  clean-exit symptom. The scheduler instead asks, once, after the tail pass: which held-out
  tail tasks are terminal and not `done`? That covers the reported incident and its siblings
  with one check.
- **Fail loud, do not re-mark pending.** The task's on-disk status is left exactly as the
  tail pass set it. Re-marking it `pending` to force a retry would hide a genuinely failing
  verification behind an infinite resume loop; the operator resumes deliberately.
- **Exit code 1, only for `full-real`.** `main()` already returns `2` for the removed
  `--sequential` flag, so `1` is free for "run completed, tail work failed". Distinguishing
  it from a crash matters for `worktrail-detach`'s exit sentinel, which records the code.
- **Journal field, not just a log line.** `failed_tail_tasks` sits beside the existing
  `pending_tail_tasks`/`unreconciled_tail_evidence` bookkeeping so the resume dashboard and
  post-run triage read it from the journal rather than by grepping a detached log.

## Risks / Trade-offs

- A caller that currently treats `full-real` rc=0 as "the process ran" will now see a
  failure. That is the point of the change, but the prose that describes launching
  `full-real` should not claim a non-zero exit means a crash -- the banner names the failed
  tail task ids.
