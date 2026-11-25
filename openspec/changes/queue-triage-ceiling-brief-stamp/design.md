## Context

See proposal.md for the gap. The mechanics this change has to respect, verified in this checkout:

- **The ceiling branch is the only no-PR landing path that reaches the brief.** `land_pr`'s
  outcomes are `landed`, `code_defect`, `review_threads_blocking`, `ceiling`, and `refused`
  (`land_pr.py:208-228`). `refused` returns through its own branch (`queue_triage.py:3290-3298`);
  the other three non-`ceiling` outcomes always populate `pr_url`; every `landed` return does too.
  So `if not pr_url:` (`:3300`) sees only a `ceiling`, and every `ceiling` return without a URL is
  post-commit: `push_ambiguous` (`land_pr.py:1596-1604`) or a refused PR create/update
  (`:1638-1682`).
- **The release is deliberate.** The existing requirement "Merged fold/propose landing tears down
  its local branch" makes the no-PR release correct: there is no pull request to close against,
  and a released brief is meant to be retryable. `release()` sets `status: queued` and
  `released-at`, removes `batch-primary`, and preserves every other field
  (`work_queue.py:1892-1960`).
- **Evaluation dedup is driven by `## Triage <date>` notes.** `is_recently_triaged()`
  (`queue_triage.py:631-651`) reads the most recent note via `triage_history()` and ignores only
  `repo-inferred`/`decision-answered`; `inventory()` excludes a recently triaged brief from the
  groups and reports it as skipped (`:905-917`). The window defaults to 25 days
  (`:4455-4462`), and `escalation_due()` bypasses it for a queue-age or keep-limit escalation
  (`:698-728`).
- **`apply` never consults that window.** `apply_verdicts()` acts on the verdicts it is handed.
  A saved `verdict.json` can therefore be replayed after the cause is fixed even while `evaluate`
  would skip the brief.
- **Frontmatter helpers already exist.** `_set_fm_fields()` (`work_queue.py:621-645`) splices
  scalar fields in place, `_resolve_brief_path()` (`queue_triage.py:2245-2258`) locates a brief in
  `queue/` or `picked/`, and unknown keys are ignored by `validate_brief`.

## Goals / Non-Goals

**Goals:**

- Make the landing ceiling's cause visible in the action log instead of an opaque no-URL string.
- Leave a durable, machine-readable and human-readable record of the failed landing on the brief
  that is released, naming the step, the cause, and the branch.
- Use that record to stop a later `evaluate` from re-proposing the same change within its dedup
  window, without making the failure unrecoverable.

**Non-Goals:**

- Re-landing, re-authoring, or inventing a PR URL.
- Keeping the brief claimed in `picked/`. No PR exists; the rejected-closure shape does not apply,
  and the stalled-in-flight resume path has nothing to close.
- Changing worktree/local-branch teardown, the `refused` branch, pre-`land_pr` failures, the
  successful closure, the rejected-closure stamps, or `evaluate`'s window and escalation rules.
- Retroactively stamping briefs already released by earlier versions (the information was never
  recorded and cannot be reconstructed).

## Decisions

- **Stamp in the no-PR ceiling branch, before the existing release.** The brief is still claimed
  at that point, so `_resolve_brief_path()` finds it in `picked/`, and `release()` later preserves
  the added fields. Stamping in the outer `finally` would need the same branch test and would tie
  bookkeeping to cleanup; doing it in the branch keeps the failure and its record adjacent.
- **Use a `## Triage <date>` note as the dedup signal.** It is the mechanism `evaluate` already
  honors, it appears in the brief's history and the run report's skipped list, and its
  `verdict:` line can name the applied verdict (`propose-change`/`fold-into-change`), which is
  accurate: the brief was evaluated and an apply was attempted. The alternatives -- a bespoke
  `landing-failed` skip in `inventory()`, or reusing `blocked-on` -- would each add a second
  suppression path with its own escape hatches; `blocked-on` is not consulted by `evaluate` at
  all.
- **Also write frontmatter fields.** `landing-failed-step`, `landing-failed-detail`, and
  `landing-branch` put the state at the top of the brief where an operator looks first and give
  tooling a stable scalar to read; the note carries the prose. The detail is whitespace-collapsed
  to one line so the frontmatter stays a plain scalar. This mirrors the
  `closure-rejected-pr`/`closure-rejected-reason` precedent from
  `queue-triage-rejected-closure-recovery`, but does not reuse those names: they mean a PR exists
  and a closure was rejected, which is the opposite of this state.
- **Fail the stamp soft, not the release.** If the brief cannot be located or the write fails, the
  entry reports `stamp_error` and the existing release still runs. Refusing to release on a
  bookkeeping failure would strand a retryable brief, which is worse than the missing stamp the
  action log now reports.
- **Keep the shared landing projection.** The new entry's `landing` sub-dict reuses the five
  fields the success path reports (`outcome`, `run`, `final_status`, `merge_result`,
  `failing_checks`) and adds `refused_step` and `detail`, so a caller reads the failure from the
  same place it already reads `landing.outcome` for `code_defect`/`review_threads_blocking`.
- **Message shape.** `land_pr <outcome> at <step>: <detail>` when a step is present;
  `land_pr <outcome>: <detail>` when only a detail is; and the previous
  `land_pr <outcome> but no pr_url returned` when neither exists. The step and detail come from
  the same `LandOutcome` fields the `refused` branch already reports.

## Risks / Trade-offs

- **The dedup window is 25 days.** A transient cause such as a missing label suppresses automated
  re-evaluation for that long. The operator's immediate remedy is replaying the saved verdict
  (`apply` is unaffected), and `escalation_due()` still forces the brief back into evaluation on
  queue age (14 days by default) or a keep-limit escalation, so the suppression is not permanent.
  `skills/worktrail-go/SKILL.md` names the replay path.
- **A stamp failure leaves the brief looking fresh.** The action-log entry says so; without the
  stamp the operator still sees the real landing cause in `error`, and the brief remains
  retryable.
- **`push_ambiguous` may record a branch that never reached the remote.** The field names the
  branch the landing used; the detail states that the remote state is unknown, which is exactly
  what the operator must reconcile. This change does not attempt to reconcile it.
