## Why

Work-queue briefs and spec/openspec-change tasks are two independent tracking systems with no
reconciliation between them in either direction. A brief can get filed for work that is already
an open, unchecked task under an active spec (two briefs filed 1 day apart independently targeted
the exact same remaining tasks 2.3/2.4/4.1/5.1 under datalena's
`onboarding-tool-dispatch-wiring/tasks.md`, caught only by manual triage reading both bodies).
And when a brief closes, nothing checks whether its work actually ticked the spec task it
describes, so the two records can silently disagree about whether the same piece of work is done
(the longer-running `docs/specs/**/TASK-*.md` frontmatter-vs-checkbox drift already documents
~46 specs / ~740 files affected by this class of problem in a sibling repo). Both gaps let the
same outstanding work get tracked, dispatched, or reported inconsistently across the two systems.

## What Changes

- Extend the shared overlap-check machinery (`overlap_check.py`'s `scan()`, reused by both
  `#overlap-check` and `check_spec_collision.py`) so that, when a brief or request already
  resolves to a known target spec/change, it additionally enumerates that spec's individual
  open (unchecked) tasks as match candidates — not just the whole-spec/whole-change candidate.
  Task-level candidate enumeration is scoped to a known target spec; it does not run a
  fleet-wide task scan when no target spec is known (see design.md Decision 1).
- Wire the new task-level candidates into the two places that already do this class of
  comparison: (a) Phase 5.5's dispatch-time `check_spec_collision.py` guard (Routes C/D/F/G),
  extended so a task-level match is handled alongside today's whole-spec `Implemented` match; and
  (b) the dashboard's existing advisory brief-clustering scan (`duplicate-brief-detection`'s
  `cluster_detect.py`, invoked from `dashboard.py`), extended so a queued brief can also cluster
  against an individual open task in an active spec it targets, not only against other briefs.
  This reaches the failure mode the discovery incident actually hit — two briefs sitting
  undispatched in the queue, only visible via the dashboard/triage pass, not via a dispatch-time
  check that neither brief had reached yet.
- On a task-level match, the affected surface (brainstorm overlap menu, Phase 5.5, or dashboard
  advisory) points the caller at the existing task instead of independent duplicate content. A
  brief created against a matched task carries an explicit `target-task:` frontmatter reference
  (new optional field, paired with the existing `target-spec:`) instead of silently duplicating
  the task's scope.
- Extend `work_queue.py done` so that, when the closing brief carries both `target-spec:` and
  `target-task:`, it reads that task's current checkbox state in the target spec's `tasks.md`
  before closing. If the brief closes as implementation-complete and the checkbox is still
  unticked, `done` surfaces a `checkbox_out_of_sync: true` warning in its result and records it
  in the closure note — it does **not** auto-tick the checkbox itself (see design.md Decision 2
  for why auto-ticking is out of scope for this change).
- The checkbox-state read reuses `openspec-stale-bookkeeping-detection`'s existing cached-RunPlan
  lookup (`conductor.runplan.load_cached` / `fingerprint`) rather than adding a second tasks.md
  parser or triggering a fresh model call; a cache miss degrades to "no signal," matching that
  capability's own best-effort contract.
- No new frontmatter field is added for capture-time reconciliation, and no new periodic sweep
  script is introduced — see design.md's Open Questions resolution for why both were considered
  and rejected in favor of extending the two existing checkpoints above.

## Capabilities

### New Capabilities
- `spec-task-work-queue-reconciliation`: task-level overlap matching between work-queue briefs
  and individual open spec/openspec-change tasks (creation-time prevention via Phase 5.5 and the
  dashboard advisory scan), plus closure-time checkbox-sync verification in `work_queue.py done`.

### Modified Capabilities
- `spec-overlap-detection`: `scan()`'s candidate enumeration gains an optional per-task
  candidate mode for OpenSpec-shaped roots, used when a target spec/change is already known,
  returning task id + task line text alongside the existing whole-change candidate shape.

## Impact

- `src/worktrail/router/overlap_check.py` — new task-candidate enumeration function for
  OpenSpec-shaped roots.
- `src/worktrail/router/check_spec_collision.py` — Phase 5.5 dispatch-time guard gains
  task-level candidates when a target spec is resolvable.
- `src/worktrail/router/cluster_detect.py` / `dashboard.py` — dashboard advisory scan gains
  brief-vs-task clustering alongside existing brief-vs-brief clustering.
- `src/worktrail/workqueue/work_queue.py` — `done()` gains closure-time checkbox-sync check;
  brief frontmatter schema gains optional `target-task:`.
- `skills/worktrail-go/references/subagent-prompts.md` (`#overlap-check`),
  `skills/worktrail-go/references/spec-collision-check.md`,
  `skills/worktrail-handoff/references/handoff-template.md` — doc updates for the new candidate
  shape and `target-task:` field.
- No new external dependency; no data migration (new frontmatter field is optional, existing
  briefs without it are unaffected).
