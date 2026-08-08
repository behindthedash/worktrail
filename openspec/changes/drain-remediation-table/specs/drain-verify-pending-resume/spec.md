## MODIFIED Requirements

### Requirement: Verify-pending sweep runs alongside the existing quarantine and stale-bookkeeping sweeps
The unattended queue-drainer SHALL run the verify-pending sweep, together with
the budget-exhausted-quarantine sweep and the stale-bookkeeping sweep, as
table entries in the shared `REMEDIATION_TABLE` remediation engine (see the
`drain-stage-remediation-table` capability), at the same points in its loop
where these sweeps already ran before this change — once before the
queue-draining loop starts, and once after it finishes when at least one
queue iteration ran. `resume_verify_pending`'s existing public signature and
return shape are unchanged; it now delegates to the shared sweep engine
scoped to only the verify-pending table entry, rather than running its own
hand-rolled loop.

#### Scenario: Drain invocation with a configured repos-root
- **WHEN** `drain()` is invoked with `--repos-root` set and not in dry-run mode
- **THEN** the quarantine sweep, the verify-pending sweep, and the
  stale-bookkeeping sweep all run before the queue loop starts, and all three
  run again after the loop finishes if the loop executed at least one
  iteration

#### Scenario: Drain invocation without a configured repos-root
- **WHEN** `drain()` is invoked without `--repos-root`
- **THEN** none of the three sweeps run, matching today's existing gating for
  the quarantine sweep

#### Scenario: `resume_verify_pending` called directly (existing test/caller path)
- **WHEN** `resume_verify_pending(repos_root, go_repo, agent, timeout,
  spawner, log)` is called directly, outside of `drain()`
- **THEN** it returns exactly the verify-pending findings' results, identical
  in shape to its pre-change behavior, without also invoking the quarantine
  or stale-bookkeeping remediations as a side effect
