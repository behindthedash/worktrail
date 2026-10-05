## Why

`_worktree_pr_close()` (`src/worktrail/workqueue/queue_triage.py:3023`) lands a
`fold-into-change`/`propose-change` verdict through `router.land_pr`, then closes the brief with
`done(v.brief_id, note=v.evidence, triaged_to=pr_url)`. Since
`openspec/changes/archive/2026-10-03-queue-triage-landed-brief-no-rollback` (PR #1414), a
rejected `done()` past the PR-exists guard leaves the brief claimed in `picked/` with
`status: picked` and reports `rolled_back: false` (`queue_triage.py:3326-3341`), deliberately:
the pull request exists, so releasing the brief would re-queue work the PR already captures.

That change removed the rollback. It did not add a closer, and none exists today:

- `grep -n 'recover' src/worktrail/workqueue/queue_triage.py` returns nothing — there is no
  recovery action anywhere in the triage path.
- The PR URL is not recoverable from anything on disk. It survives only in the action-log entry
  `apply` prints (`cmd_apply`, `queue_triage.py:4317-4352`); no caller persists that log — the
  drain prepass prints it and raises on the non-zero exit
  (`drain.py:2391-2395`) — and the brief itself is byte-unchanged on this path, because
  `done()` returns before mutating when a gate rejects it (`work_queue.py:1651-1700`). So after
  the rejecting process exits, "which PR did this brief land in" is unrecoverable from the
  queue.
- The only closer left is the dashboard's stalled-in-flight item
  (`router/dashboard.py:2498` `inflight_briefs`, built into the picker at :3077-3093), whose
  whole vocabulary is "Resume this stalled brief — claimed long ago with no completion; likely
  an abandoned session." A rejected-closure brief is not an abandoned session: its work has
  landed. The operator has to reconstruct the PR by hand before they can close it.

The rejection itself is a known, documented shape rather than a mystery: `done()` refuses a
closure note that asserts a re-verification result with no shown transcript
(`_reverification_claim_missing_evidence`, `work_queue.py:208-211`, gate called at :1651), and
queue-triage hands the evaluator's raw evidence prose in as that note
(`work_queue.py:186-192` records the identical
incident — brief 20261002-221651, observed live 2026-10-02 on brief 20261002-203443, "rolled the
brief back to `queue/` after its PR had already merged"). The note that trips the gate is not a
closure justification at all — the justification is the landing PR, which `done()` already
accepts as `triaged_to`. Before #1414 that mismatch re-queued already-merged work; after #1414 it
strands the brief with no closer. These are the two halves of one defect, and this change closes
the second half.

## What Changes

- **The rejected-closure landing is recorded on the brief.** When `apply` obtains a PR URL and
  the following `done()` does not complete, it additionally stamps `closure-rejected-pr:` (the
  `pr_url` it already has) and `closure-rejected-reason:` (the `done()` status) onto the claimed
  brief before returning. The brief's `status` stays `picked`; nothing else about it changes. The
  stamp is a durable historical fact — it stays after recovery, alongside `triaged-to`.
- **One command closes such a brief.** New `worktrail-queue-triage recover-closure --brief <id>
  [--evidence <note>] [--dry-run] [--json]` re-attempts the closure as `done(brief_id,
  triaged_to=<recorded PR>, note=<--evidence or none>)` and reports the outcome. With no
  `--evidence` the closure is justified by the landed PR alone, which is what the triage closure
  is; `--evidence` exists for the briefs whose gate still refuses without a note (a
  consolidation batch must name and evidence every sub-item). It never releases the brief, never
  re-triages, and spawns no agent.
- **Fail closed.** Missing/claimed-elsewhere brief, no recorded landing, a recorded value that
  is not a pull request URL, or a still-refused closure each leave the brief byte-identical and
  exit non-zero with the specific reason.
- **The dashboard stops calling these briefs stalled sessions.** `inflight_briefs` carries the
  recorded landing through, and `build_category_items` surfaces such a brief as
  `action: "recover-closure"` (naming the PR) instead of the generic `resume`. A brief carrying a
  recorded landing is surfaced regardless of claim age — the closure is outstanding, not
  abandoned.
- **Non-goals:** re-running the triage evaluation, re-landing anything, releasing or re-queueing
  the brief, manufacturing closure evidence (the recovery never synthesizes a transcript), and
  changing `done()`'s gates or `_worktree_pr_close()`'s no-PR release path.

## Capabilities

### New Capabilities

- `queue-triage-rejected-closure-recovery`: one command that closes a brief a rejected closure
  left claimed, against the landing PR already recorded on it, plus the dashboard action that
  points an operator at it.

### Modified Capabilities

- `queue-triage`: the "Merged fold/propose landing tears down its local branch" requirement gains
  the contract that the rejected closure records its landing PR and reason on the brief, so the
  outstanding closure is recoverable from the queue alone.

## Impact

- `src/worktrail/workqueue/queue_triage.py`: `_worktree_pr_close()`'s rejection branch stamps the
  recorded landing; new `recover_rejected_closure()` and the `recover-closure` subparser.
- `src/worktrail/router/dashboard.py`: `inflight_briefs()` annotation and the
  `build_category_items()` action for a brief carrying a recorded landing.
- `skills/worktrail-go/SKILL.md` (action table) and
  `skills/worktrail-go/references/dashboard-render.md` (`action` vocabulary, `inflight` shape).
- `tests/workqueue/test_queue_triage.py`, `tests/workqueue/test_queue_triage_recover_cli.py`,
  `tests/router/test_dashboard.py`.
- No new console script, policy key, dependency, journal schema, or frontmatter requirement;
  `validate_brief` checks only a fixed required set (at most `id`/`status`/`focus`) and ignores
  unknown keys, and the two new fields are written through the existing `_set_fm_fields`.
