## Why

`worktrail-go auto` only ever claims work-queue briefs, so two backlog shapes are invisible to
unattended drains until a human notices them on the dashboard and captures a brief by hand:

- **Specs that need tasks** — a spec folder in the `needs-tasks` dashboard stage (approved
  `spec.md`, no unresolved clarification markers, no task DAG). Its next action
  (`spec-to-tasks`) is fully deterministic, yet nothing ever schedules it.
- **Epics with unspecced features** — an epic under `docs/specs/epics/` whose `### Feature`
  decomposition lists more features than there are specs citing the epic id. Route B doctrine
  assigns feature spec ids only at Route C pickup, so nothing on disk ever converts "epic has
  remaining features" into schedulable work.

The drain's remediation sweep already resumes interrupted pipeline states (quarantines,
verify-pending, sync-pending, stale-bookkeeping), but planning backlog was explicitly out of its
scope. The operator asked for exactly this: "epics that need specs, and specs that need tasks
need to feed the queue proactively."

## What Changes

- New `workqueue/seed_backlog.py` (`worktrail-seed-backlog` console script): finds `needs-tasks`
  specs (via the dashboard scan, both devkit and OpenSpec rows) and under-specced epics (feature
  headings vs citing-spec count), and captures planning-only Route C briefs for them via
  `create_handoff`.
- Every seeded brief carries a `seeded-from:` frontmatter key; a key already present anywhere in
  queue/ or picked/ (any status) is never seeded again. Spec keys are stable; epic keys embed the
  citation count so real progress re-arms seeding while a fruitless completed brief terminates
  the sequence instead of looping.
- Epics with a terminal `**Status:**` line are skipped; epics with no parseable `### Feature`
  decomposition are reported, never seeded (no terminal condition would exist).
- Seeding is capped per sweep (default 5) with the dropped count logged, never silently
  truncated; candidate order is deterministic (needs-tasks specs first, then epics, sorted).
- `worktrail-drain` runs the seeder pre-loop (after the remediation sweep, before the first
  ready-count check) so seeded briefs drain in the same pass; `--no-seed-backlog` opts out;
  seeding failures are logged and never abort the drain; the run summary gains a
  `seeded_backlog` key.
- `create_handoff` gains an optional `seeded_from` parameter that stamps the frontmatter key.

## Capabilities

- `backlog-seeding` (new)

## Impact

- `src/worktrail/workqueue/seed_backlog.py` (new), `src/worktrail/workqueue/create_handoff.py`,
  `src/worktrail/drain/drain.py`, `pyproject.toml` (`worktrail-seed-backlog` entry point),
  `tests/workqueue/test_seed_backlog.py` (new), `tests/drain/test_drain.py`,
  `tests/conftest.py` (suite-wide `WORK_QUEUE_DIR` isolation).
