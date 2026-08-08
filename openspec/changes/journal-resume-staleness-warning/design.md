## Context

`_pipeline_scheduler`'s resume branch (`src/worktrail/orchestrator/live.py`,
`if resume and Path(journal_path).exists():`, ~line 3378) already reconciles
the prior journal, reports done/in-flight task ids, and calls
`_resume_drift_report` (~line 1057) to print an informational line when
`base` has moved commits since a task branch's original fork point.
`_resume_drift_report` intentionally returns after the **first** task branch
it finds — it is a general "conflicts are more likely" heads-up, not scoped
to quarantine outcomes, and it does not mention `--fresh`.

Group-level state lives separately in `groups_journal` (in-memory mirror of
`journal["groups"]`, persisted by `_record_group_fn`). A group's record is
`{"pr_url": str, "head_branch": str, "state": str}` — `state` is set to
`"QUARANTINED"` at several call sites (budget exhaustion, merge conflict,
failed smoke test, integration error) but carries no reason text; a
structured `quarantine_reason` field does not exist yet (tracked separately,
brief `20260807-135614-worktrail-s-orchestrator-marks-a`).

## Goals / Non-Goals

**Goals:**
- On resume, when the journal shows one or more `QUARANTINED` groups, tell
  the operator explicitly that resuming may be replaying a stale verdict
  against a base branch that has since moved, and recommend `--fresh`.
- Compute "moved since" using each quarantined group's own task branches
  (not just the first task branch in the spec, which may belong to an
  unrelated, non-quarantined group).
- Keep this additive and non-blocking: no new hard stop, no schema change to
  the journal.

**Non-Goals:**
- Distinguishing *why* a group was quarantined (budget exhaustion vs. real
  failure) — that's the separate `quarantine_reason` brief.
- Auto-triggering a fresh run. The operator decides.
- Changing `_resume_drift_report`'s existing generic message or call site.

## Decisions

- **New helper, not a rewrite of `_resume_drift_report`.** Add
  `_resume_quarantine_staleness_warning(repo, base, spec_id, groups_journal)`
  next to `_resume_drift_report` in `live.py`, called immediately after the
  existing `_resume_drift_report(...)` call inside the `resume and
  Path(journal_path).exists()` block. Keeping it a separate function (rather
  than folding quarantine-awareness into `_resume_drift_report`) avoids
  changing that function's existing single-task-branch short-circuit
  behavior, which other resume paths may depend on for cheap best-effort
  output.
- **Iterate every `QUARANTINED` group, not just the first.** For each group
  name with `state == "QUARANTINED"` in `groups_journal`, resolve its
  member task branches (`f"{spec_id}/{t['id'].lower()}"` for tasks belonging
  to that group — group membership is already known to the scheduler via the
  same `tasks`/grouping data `_pipeline_scheduler` holds) and compute the
  merge-base drift against `base` the same way `_resume_drift_report` does
  (`git merge-base <branch> <base>`, then `git rev-list --count
  <merge-base>..<base>`). Use the **maximum** drift count across a group's
  branches (worst case = most likely stale) as that group's reported drift.
- **Threshold: any non-zero drift.** No new "meaningful" magnitude constant.
  Matching `_resume_drift_report`'s own `if n != "0":` gate keeps the two
  messages consistent and avoids inventing an arbitrary N with no evidence
  behind it — a single commit landing on `base` after a quarantine can be
  exactly the fix that unblocks the group (the datalena spec-100 case: one
  FK-blocker-fixing commit).
- **Message is a distinct, loud line** (not a variant of the existing drift
  line), e.g.:
  `PIPELINE RESUME WARNING: group '<name>' is QUARANTINED in the resumed
  journal, and base '<base>' has moved <n> commit(s) since that group's task
  branch was forked. This resume will replay the prior quarantine verdict
  as-is. If the blocker may already be fixed on <base>, re-run with --fresh
  to re-evaluate instead of trusting the cached result.`
  printed once per stale quarantined group, before
  `=== PIPELINE RUN COMPLETE ===`.
- **Best-effort, matching `_resume_drift_report`'s failure posture.** Any
  git-call failure (missing branch, non-zero exit) skips that group's
  warning silently rather than raising — this is an operator heads-up, not a
  gate, and must never block or fail a resume.

## Risks / Trade-offs

- [Risk] A group can be `QUARANTINED` for a reason unrelated to `base` drift
  (e.g. a flaky external service) — the warning could fire on a resume where
  `--fresh` would not actually help. → Mitigation: the message is phrased as
  a conditional recommendation ("if the blocker may already be fixed"), not
  a claim that `--fresh` will succeed; the operator still decides.
- [Risk] Iterating every quarantined group's branches adds a few `git`
  subprocess calls to the resume path. → Mitigation: bounded by the number
  of quarantined groups (typically small), and only runs on resume with an
  existing journal — not on a fresh run or the common all-green resume case
  (no `QUARANTINED` groups → no extra calls).
- [Trade-off] This does not persist the warning into the journal itself
  (proposal originally considered that); keeping it print-only avoids a
  journal schema change and keeps this change scoped to the resume-path
  bug it fixes.

## Migration Plan

No migration — additive behavior on an existing code path, gated on
conditions (`resume=True`, existing journal, `QUARANTINED` groups present,
non-zero branch drift) that are all false on every run today with no
`QUARANTINED` groups in its journal. No flag needed; this is a bug fix to
the existing resume message, not new opt-in behavior.

## Open Questions

None outstanding — scope, message shape, and drift computation are decided
above.
