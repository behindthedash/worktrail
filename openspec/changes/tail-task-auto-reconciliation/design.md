## Context

See proposal.md - Why. Relevant existing machinery this design builds on
(`src/worktrail/orchestrator/integrate.py` unless noted):

- `detect_unreconciled_tail_evidence(repo, remote, base, spec_id, wt_base, tasks)`
  — pure detection, already shipped (PR #330). Returns
  `[{"task": tid, "worktree": <path>, "head_sha": <short sha>}, ...]` for every
  terminal (`status` in `coordinator.DONE`) tail-kind task whose per-task
  worktree branch HEAD is not an ancestor of `<remote>/<base>`. Unchanged by
  this design.
- `integrate_one(g, repo, spec_id, tasks, remote, run_id, base, journal_path,
  status, group_branch, quarantined, ...)` — integrates exactly one "group"
  (`{"name", "tasks": [task_id, ...], "depends_on": [...], "reqs": [...]}`):
  computes the deliverable subset, creates/reuses a stacked branch
  `<run_id>/<name>`, merges each deliverable task's own branch
  (`<spec_id>/<task_id.lower()>`) into an isolated integration worktree,
  handles merge conflicts (auto-resolve `__init__.py` add/add, else spawn an
  assembly-resolve worker, else abort and quarantine), pushes, and
  reconcile-safely creates-or-reuses a PR against `base`. Writes a per-group
  record into `journal["groups"][name]` (`pr_url`, `head_branch`, `state`,
  optional `quarantine_reason`). Fully generic over group shape and task
  count — nothing in it assumes the group came from `coordinator.plan_groups`.
- `coordinator.plan_groups(tasks)` only ever plans over
  `tasks with kind not in TAIL_KINDS` — it never produces a group for a
  tail-kind task, and never will (tail tasks are held out of the fan-out by
  design). This is why reconciliation needs its own synthetic group
  construction rather than being able to route tail tasks through
  `plan_groups`.
- `quarantine_selfcheck.check_repo()` iterates `journal["groups"].items()` and
  reports every entry with `state == "QUARANTINED"` — keyed purely by the
  group name string in the journal, not by cross-referencing
  `plan_groups()`'s current output. A synthetic group name that never appears
  in `plan_groups()` is still picked up as a finding.
- `journal_selfcheck.check_repo()` reads `journal["unreconciled_tail_evidence"]`
  (a plain list written by `_record_unreconciled_tail_evidence`) only to build
  one fixed `detail` string per journal file; it does not currently look at
  per-finding fields.
- Both `full-real` schedulers call detection + recording at the same point,
  right after the tail dispatch settles: `_pipeline_scheduler` (live.py
  ~4122-4128) and `_full_real_inner` (live.py ~4762-4768). Both already have
  `repo`, `remote`, `base`, `spec_id`, `run_id`, `journal_path`, `pr_labels`,
  `route`, `gates` in scope at that point (all threaded into the preceding
  `finish_real`/pipeline-integrate calls).

## Goals / Non-Goals

**Goals:**
- Turn a detected `unreconciled_tail_evidence` finding into an actual PR
  automatically, using `integrate_one` unmodified.
- Make the reconciliation attempt itself resume-safe (safe to call again on a
  later run over the same journal/branches) by relying entirely on
  `integrate_one`'s existing reconcile-safe behavior (MERGED short-circuit,
  OPEN-PR reuse, remote-branch reuse) — add no new idempotency bookkeeping.
- Make the outcome legible without a dashboard code change, by writing into
  fields the existing generic detectors already read (`journal["groups"][*]`)
  plus enriching the one field `journal_selfcheck.py` already parses
  (`unreconciled_tail_evidence`).

**Non-Goals:**
- No new conflict-resolution strategy. A tail-branch merge conflict is
  handled exactly like any other group's merge conflict today (abort, record
  `QUARANTINE_MERGE_CONFLICT`, surface via `quarantine_selfcheck.py`).
- No change to `detect_unreconciled_tail_evidence`'s detection semantics — it
  keeps reporting ground truth (is this task's HEAD an ancestor of
  `<remote>/<base>` right now), not "has reconciliation been attempted."
- No automatic worktree/branch cleanup after a successful reconciliation PR
  merges. Tail-task worktree lifecycle is unchanged; nothing today
  automatically deletes them (confirmed: no cleanup call references tail
  worktrees outside the impl-group `cleanup_group` path in `verify.py`), and
  this change does not add one. Out of scope — a genuinely separate concern
  from "did the commits reach base."
- No fix to `quarantine_selfcheck.py`'s secondary auto-close path
  (`_group_files` / `reconcile_finding`) for synthetic tail-group names — see
  Risks below.

## Decisions

**Synthetic single-task group, not a real `plan_groups()` group.**
`reconcile_unreconciled_tail_evidence(findings, repo, spec_id, tasks, remote,
run_id, base, journal_path, pr_labels=None, route=None, gates="")` builds, per
finding, `g = {"name": f"tail-{task_id.lower()}", "tasks": [task_id],
"depends_on": [], "reqs": [...task's reqs...]}` and calls `integrate_one(g,
...)` directly — it does not go through `plan_groups`/`finish_real`'s
per-group loop. `depends_on: []` means `integrate_one` always targets `base`
directly (never a stacked dependent branch), which is correct: reconciliation
only ever runs after `_dispatch_pending_tail` confirms every impl group is
already terminal, so `base` is the right integration point for a tail task's
own commits. Alternative considered: extend `coordinator.plan_groups` to emit
tail groups and route them through the standard `finish_real` group loop —
rejected because tail tasks are deliberately excluded from the parallel
fan-out's dependency/file-collision planning (they run after, not alongside,
impl groups), and `plan_groups`'s union-find grouping logic has no meaning for
a single already-terminal task; a bespoke one-task group per finding is
simpler and does not risk misgrouping two unrelated tail tasks together.

**One `integrate_one` call per finding, not one group of every finding.**
Each tail task gets its own PR rather than batching all unreconciled tail
tasks into one PR. Rationale: tail tasks are typically independent (e2e vs.
cleanup, or one per parallel spec run); batching would mean one bad task's
merge conflict blocks every other task's otherwise-clean reconciliation, which
directly contradicts the "one bad group must not block the rest" posture
`finish_real`'s per-group loop already has for impl groups.

**Reconciliation runs unconditionally whenever findings are non-empty, at the
same two call sites detection already runs at.** No feature flag, no opt-out,
no `--only`-style filtering for v1: mirrors detection's own posture (always
on) and keeps this a drop-in continuation of the existing flow rather than a
second knob operators must learn. `pr_labels`, `route`, `gates` are forwarded
unchanged so the reconciliation PR gets the same label/policy treatment as any
other group PR.

**Journal write shape: reuse `integrate_one`'s own per-group journal record,
and enrich the existing `unreconciled_tail_evidence` list rather than
replacing it.** After calling `integrate_one` for a finding, read back
`journal["groups"][f"tail-{task_id.lower()}"]` (already written by
`integrate_one`'s internal `_do_journal`/`_write_group_journal`) and copy its
`pr_url` and `state` onto that finding's dict before
`_record_unreconciled_tail_evidence` persists the list:
`{"task": ..., "worktree": ..., "head_sha": ..., "reconcile_state": <state
mapped to opened|already-open|merged|quarantined>, "reconcile_pr_url": <url
or "">}`. Rationale for enriching in place rather than adding a parallel
`tail_reconciliation` journal key: `journal_selfcheck.py` already parses
`unreconciled_tail_evidence` as the single source for this finding kind: one
field to read, one place a future reader looks. Alternative (a separate
`tail_reconciliation_results` list keyed by task) was rejected as needless
indirection when the finding and its outcome are 1:1 per task.

**`journal_selfcheck.py` message logic:** if `reconcile_state` is `opened` or
`already-open`, emit an informational detail ("...auto-reconciliation PR open
(<url>), awaiting merge") instead of "reconcile before the worktree is cleaned
up." If `reconcile_state` is `merged`, drop that task from the message
entirely (it is, in fact, reconciled — though in practice `detect_...` will
stop reporting it once merged, since HEAD becomes an ancestor of base; this
branch exists only for the narrow window where the journal was enriched from a
still-open finding whose PR merged between detection and the next read, which
`check_repo` handles by re-reading the current journal each call, so it's a
belt-and-suspenders case, not a load-bearing one). If `reconcile_state` is
`quarantined` or missing/absent, keep today's "needs manual/human triage"
tone.

## Risks / Trade-offs

**[Risk] A synthetic `tail-<task-id>` group that gets quarantined is visible
via `quarantine_selfcheck.py`'s primary finding path, but its secondary
auto-close check (`_group_files`, which recomputes a group's file set by
looking the group name up in `coordinator.plan_groups(tasks)`'s current
output) will always return `None` for it, because `plan_groups` never
produces tail groups.** → Mitigation: this fails open, not silently — the
finding still surfaces in `result["findings"]`, it just never gets a chance to
auto-clear itself via file-presence-on-base the way a real impl group's
quarantine can. Accepted for v1: a human/agent seeing the quarantine finding
still has the full context (`pr_url`, `quarantine_reason`) to resolve it
manually, and `quarantine_selfcheck.py` is unmodified per this change's
"reuse without a dashboard change" goal. If this proves noisy in practice,
teaching `_group_files` to special-case a `tail-` prefix (return the single
task's `files` from `tasks`) is a small, separable follow-up.

**[Risk] Reconciliation could open a PR for a tail task whose commit was
"real work" the operator did not intend to ship as its own standalone
change** (e.g. a cleanup task's evidence file was meant only for the run's
own bookkeeping, not a durable repo artifact). → Mitigation: this is exactly
the same shipping decision `integrate_one` already makes for every impl-group
task today (does DONE mean "ship it"), so it introduces no new class of risk
— the existing PR review gate (a human still merges the PR; auto-merge policy
is unchanged and orthogonal to this change) is the safety net, same as any
other orchestrator-opened PR.

**[Risk] Calling `integrate_one` twice for the same tail task across two
different call sites in the same process run** (pipeline scheduler vs.
sequential path) is not possible in practice — `full-real` picks exactly one
scheduler per run — but a resumed run re-invoking the same call site a second
time will call `reconcile_unreconciled_tail_evidence` again for any
still-unreconciled finding. → Mitigation: this is precisely what
`integrate_one`'s reconcile-safe design (MERGED short-circuit / OPEN-PR reuse
/ remote-branch reuse) already exists to make safe for every other group; no
new mitigation needed, and the "Reconciliation is safe to retry" spec
requirement exists specifically to pin this down as a tested guarantee rather
than an assumption.

**[Trade-off] One PR per unreconciled tail task, not one combined PR.** More
PR-creation calls and more `gh` round-trips when multiple tail tasks are
unreconciled in the same run (uncommon — most specs have one e2e and/or one
cleanup task). Accepted: independence of failure (see Decisions) outweighs
the minor extra API cost, and matches the existing one-PR-per-group posture
operators already expect from `full-real` runs.

## Migration Plan

No migration — this is new behavior gated entirely on
`detect_unreconciled_tail_evidence` returning a non-empty list, which today
happens only in the already-flagged production incident case. Existing runs
with no unreconciled tail evidence are unaffected. No schema change to
existing journal fields other than the additive `reconcile_state` /
`reconcile_pr_url` keys inside each `unreconciled_tail_evidence` list entry
(readers that only checked "is this list non-empty," including today's
`journal_selfcheck.py` before this change, keep working unmodified against
old journals that lack the new keys).

Rollback: revert the `live.py` call-site wiring and the new
`integrate.reconcile_unreconciled_tail_evidence` function; detection and
recording (PR #330) continue to work exactly as they do today since they are
unmodified by this change.
