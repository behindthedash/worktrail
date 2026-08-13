## Why

The work queue (`$WORK_QUEUE_DIR/queue/`) accumulates deferred-work briefs faster than any
human reviews them for continued relevance. A supervised pilot triage pass run by hand on
2026-07-31 proved the judgment is soundly automatable — 6 parallel evaluator agents assessed
65 briefs (repo-grouped, evidence-required verdicts, fail-open on undecidable), and the
operator approved 100% of the resulting kill list (queue went from 75 to 57 briefs). That run
also surfaced concrete prompt-template lessons (require repo fetch first, cite evidence, cap
tool calls, check for archived/renamed target repos, check memory before flagging false
alarms) that only exist today as prose in a handoff brief. Without codifying the pilot, every
future triage pass repeats the same manual, unrepeatable, un-reviewable process — or, more
likely, never happens again and the queue keeps growing stale.

## What Changes

- Add a new `queue-triage` capability to `worktrail`: a script that inventories
  `$WORK_QUEUE_DIR/queue/`, groups briefs by their `repo:` frontmatter, and spawns one
  headless evaluator agent per repo group (reusing `orchestrator/spawnlib.py`'s
  `spawn_agent`) to produce an evidence-required verdict per brief: `keep`, `stale-close`,
  `needs-update`, or `duplicate-of <id>`.
- The evaluator prompt template bakes in the 2026-07-31 pilot's lessons: fetch the target
  repo before judging, cite PR/commit/file evidence, a bounded tool-call budget per brief
  (~3-4), fail-open to `keep` on any undecidable case, an explicit archived/renamed-repo
  check (`gh repo view --json isArchived`) before trusting an absent file as evidence of
  staleness, and an instruction to check locally-available memory files before treating an
  observed operational state as an "alarm" worth flagging.
- Aggregate the per-group verdicts into (a) a human-readable Markdown report and (b) a
  machine-applyable verdict file (JSON).
- Add a separate `apply` step, invoked only after explicit operator review of the report,
  that executes approved verdicts against the queue: `stale-close`/`duplicate-of` verdicts
  claim then complete the brief (via `work_queue.py`'s existing `claim`/`done`) with the
  verdict's evidence recorded as the closure note; `needs-update` verdicts append a
  `## Triage <date>` section to the brief in place. The apply step never closes or edits a
  brief without it appearing, approved, in the verdict file the operator reviewed — there is
  no auto-apply path.
- Add a dedup marker: a brief already carrying a `## Triage <date>` section newer than a
  configurable N days is skipped by the inventory step (not re-evaluated), so a recurring
  triage cadence doesn't re-spend tokens re-judging a brief just reviewed.
- Document a recommended run cadence (monthly, or pre-drain-weekly) — deliberately not
  nightly, since a full evaluation pass costs on the order of 1M tokens and queue churn does
  not justify more frequent runs.

## Capabilities

### New Capabilities
- `queue-triage`: repo-grouped, evidence-required staleness/duplication triage of the work
  queue, split into an `evaluate` step (spawns evaluator agents, produces a report + verdict
  file) and an operator-approval-gated `apply` step (executes approved verdicts against the
  queue).

### Modified Capabilities
(none — this is additive; `work_queue.py`'s `claim`/`done`/`list_queue` and
`spawnlib.py`'s `spawn_agent` are consumed as-is, not changed)

## Impact

- New module `src/worktrail/workqueue/queue_triage.py` + console script
  `worktrail-queue-triage` (`[project.scripts]` in `pyproject.toml`).
- New tests under `tests/workqueue/test_queue_triage.py`.
- No changes to `work_queue.py`'s public API, `spawnlib.py`, or `drain.py` — this is a new
  consumer of both, not a modification of either.
- No plugin/skill surface change: this is a CLI script, not a `/go`-dispatched route: an
  operator (or a cron/scheduled invocation) runs it directly, independent of the `/go` front
  door and its route classification.
