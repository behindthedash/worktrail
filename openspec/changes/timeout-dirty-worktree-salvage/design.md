## Context

See proposal.md for the incident evidence. `live_run_real` and the pipeline
scheduler each catch `subprocess.TimeoutExpired` before trying to parse a
worker report. Both currently write a failed journal entry immediately.
`salvage_report()` can synthesize a successful implement/fix report only when
the task branch's HEAD advanced after the spawn began. The timeout path is
therefore unable to use a complete dirty worktree, and the two execution paths
currently have different timeout bookkeeping.

## Goals / Non-Goals

**Goals:**

- Recover only a task's own, declared implementation or fix changes and pass
  them into the existing review gate.
- Keep refusal safe and actionable: no partial staging, no implicit scope
  widening, and no claim that a timed-out worker completed validation.
- Give direct and pipeline execution the same recovery decision.

**Non-Goals:**

- Determining why the recorded worker ran for 1800 seconds; the available
  transcript does not establish a cause.
- Retrying timed-out workers, changing timeout defaults, or salvaging review
  and cleanup verdicts.
- Recovering an arbitrary pre-existing dirty worktree or changing retention and
  quarantine policy after a refused recovery.

## Decisions

- **Use one timeout-recovery helper before failure journaling.** It will first
  retain the existing advanced-HEAD behavior, then inspect a non-advanced
  implement/fix worktree for dirty content. Both timeout handlers call this
  helper and either commit a recovered result through their normal state
  transition or retain their failure path. This avoids one scheduler silently
  discarding work that the other preserves. Duplicating the check in both
  handlers was rejected because the paths have already drifted in their usage
  accounting and messages.

- **Require total scope coverage before staging.** The helper will derive every
  changed path from Git's machine-readable status output, normalize it like the
  task-file scope, and require each path to be an exact declared path. It stages
  and commits only after that whole-set validation succeeds. Selectively
  committing in-scope paths while leaving another change behind was rejected:
  it conceals a potentially material edit and leaves a branch whose recovered
  commit misrepresents the timed-out worker's state. Ambiguous or unsupported
  status entries fail closed.

- **Reuse the existing synthetic-report contract.** A successful recovery
  produces the same implement/fix success shape as committed-work salvage, with
  `tests: "none"` and notes that distinguish a timeout salvage. This feeds the
  existing pre-commit backstop, review, journal transition, and cleanup logic
  instead of adding a timeout-specific fast path. Inventing a `review_status`
  or marking the task done was rejected because review evidence cannot be
  inferred from a dirty diff.

- **Preserve failure evidence on refusal.** The timeout journal message and
  console output identify a recovery refusal and its offending path or reason;
  the worktree remains untouched for human inspection. This is safer than
  deleting, resetting, or force-removing a worktree after timeout.

## Risks / Trade-offs

- [A task's declared scope omits a legitimate changed path] → recovery refuses
  instead of silently committing it; an operator can inspect the retained
  worktree and correct the plan or recover the work explicitly.
- [Git status includes rename, deletion, or untracked edge cases] → parse and
  test those entries conservatively; any entry that cannot be mapped exactly
  fails closed.
- [The automatic commit captures an incomplete implementation] → it enters the
  existing independent review and cleanup flow and reports no tests as run;
  recovery preserves work, not a success verdict for the change.

## Migration Plan

No data migration or configuration rollout is required. The behavior takes
effect for newly timed-out workers. Rollback is a code revert; pre-existing
quarantined worktrees and journals remain unchanged.
