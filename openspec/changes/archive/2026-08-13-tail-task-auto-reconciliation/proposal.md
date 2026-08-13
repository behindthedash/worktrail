## Why

PR #330 added `integrate.detect_unreconciled_tail_evidence()`: after a tail-kind
(e2e/cleanup) task reaches DONE, it checks whether that task's own worktree branch
carries commits that never made it onto base. Tail tasks are held out of the
parallel fan-out's group/PR machinery by design (`PENDING_TAIL_REASON`) and each
runs in its own stacked per-task worktree with nothing merging, pushing, or
opening a PR for it afterward — so a spawned worker's real commit there (an
evidence file, a doc update, the task's own `tasks.md` checkbox flip) sits on a
throwaway branch that a later worktree-cleanup pass deletes with zero trace,
while the run still reports full completion.

Today that gap is flagged only: the finding is written to the run journal
(`unreconciled_tail_evidence`) and surfaced by `journal_selfcheck.py`'s
`unreconciled-tail-evidence` finding on the `go` dashboard, but a human has to
notice it and reconcile manually — a recurring per-run manual-intervention cost
across every consuming repo (datalena, GGB, worktrail itself, devops) that uses
OpenSpec e2e/cleanup-kind tasks. This was deliberately deferred out of PR #330 as
materially riskier for a single change; this change closes that gap now that
detection has proven itself in production (finding reproduced 2026-08-12).

## What Changes

- Add `integrate.reconcile_unreconciled_tail_evidence()`: for each finding from
  `detect_unreconciled_tail_evidence()`, build a synthetic single-task group
  (`{"name": "tail-<task-id>", "tasks": [<task-id>], "depends_on": []}`) and call
  the existing `integrate_one()` against it — the same per-group PR machinery
  used for impl groups (reconcile-safe branch/PR creation, conflict handling via
  abort-and-quarantine, journal recording). No new merge or conflict-resolution
  logic is introduced; a merge conflict integrating the tail task's own branch
  quarantines that synthetic group exactly the way a merge conflict quarantines
  any other group today.
- Wire this into both `full-real` schedulers (pipeline and sequential) in
  `live.py`, immediately after `detect_unreconciled_tail_evidence()` /
  `_record_unreconciled_tail_evidence()` — the two spots that already run at the
  end of every real orchestrator run.
- Enrich the `unreconciled_tail_evidence` journal findings with the outcome of
  the reconciliation attempt (`reconcile_state`: `opened` / `already-open` /
  `merged` / `quarantined`; `reconcile_pr_url` when a PR exists) so a human
  reading `journal_selfcheck.py`'s dashboard finding can tell "a PR is already
  open, awaiting merge/CI" from "auto-reconciliation itself failed, needs manual
  triage" without opening the journal file.
- Update `journal_selfcheck.py`'s `unreconciled-tail-evidence` finding message to
  reflect the enriched state instead of the current fixed "reconcile before the
  worktree is cleaned up" text, which is stale once reconciliation is automatic.
- A synthetic tail group that gets quarantined (merge conflict, push failure,
  `gh pr create` failure) is already picked up by the existing
  `quarantine_selfcheck.py` sweep with zero code change — it reads
  `journal["groups"][*]` generically by `state == "QUARANTINED"`, not by a
  fixed set of group names. This change does not need to touch that file, but
  does note in design.md the one known limitation: its secondary
  auto-close-via-file-presence check (`_group_files`) cannot resolve a
  `tail-<task-id>` group name because `plan_groups()` never produces tail
  groups, so the quarantine finding will not self-clear that way even after the
  underlying files land on base by other means — it still surfaces to a human
  (fails open), it just cannot auto-close via that one path.

## Capabilities

### New Capabilities
- `tail-task-auto-reconciliation`: automatic PR-based integration of a
  completed tail-kind (e2e/cleanup) task's own unreconciled commits onto base,
  and the journal/dashboard reporting of that reconciliation attempt's outcome.

### Modified Capabilities
(none — `dashboard-selfcheck` covers a different self-check; the
`unreconciled-tail-evidence` finding text this change updates was added by PR
#330 outside any OpenSpec capability spec, so there is no existing spec-level
requirement to modify)

## Impact

- `src/worktrail/orchestrator/integrate.py` — new
  `reconcile_unreconciled_tail_evidence()` function; `integrate_one()` itself is
  unchanged (reused as-is).
- `src/worktrail/orchestrator/live.py` — both `full-real` call sites
  (`_pipeline_scheduler`'s tail-dispatch path and `_full_real_inner`'s sequential
  path) call the new reconcile function and pass its result into the enriched
  journal write.
- `src/worktrail/router/journal_selfcheck.py` — `unreconciled-tail-evidence`
  finding message reads the enriched per-finding reconciliation fields.
- `tests/orchestrator/test_integrate_complete.py`,
  `tests/router/test_journal_selfcheck.py` — new coverage for the reconcile
  function and the updated finding message.
- No dashboard code changes: `quarantine_selfcheck.py` already surfaces any
  `QUARANTINED` journal group generically; a synthetic `tail-<task-id>` group is
  a `QUARANTINED` group like any other.
