## Why

The OpenSpec `tasks.md` parser only recognizes a checkbox carrying an `N.M` id
(`- [ ] 1.1 ...`). `src/worktrail/taskformats/openspec/schema.py:40`'s `TASK_RE` requires that
id, so every other checkbox falls through to `schema.py:258-260`, which appends
`checkbox without an 'N.M' id is not trackable` as a warning and emits no `ParsedTask`.
Reproduced directly: feeding `## 1. Setup\n- [ ] Do the first thing\n- [x] Do the second thing`
to `parse_tasks_md` yields `tasks=0` and two untrackable warnings even though the author clearly
listed two pieces of work.

A zero-task parse is indistinguishable downstream from "this change has no task list at all":
`router/dashboard.py:1490` maps `not tasks` to
`stage="needs-tasks", next_action="create tasks"`, and `workqueue/seed_backlog.py`'s
`find_needs_tasks_specs` turns every `needs-tasks` change into a planning-only "run
spec-to-tasks" brief. Queue-triage then works a brief that asks it to create a task DAG which
already exists, while the authored checkboxes stay invisible to worktrail and to OpenSpec's own
tracker. Nothing anywhere in `src/` normalizes or repairs the ids.

(Work-queue brief `20261125-033648-openspec-changes-whose-tasks-md`.)

## What Changes

- The OpenSpec parser records untrackable **top-level** checkboxes as structured entries
  (line number + text) in addition to the existing warning, so a zero-task parse can be told
  apart from an empty `tasks.md`. Indented checkboxes remain tolerated sub-items.
- A deterministic, idempotent normalizer assigns `N.M` ids under the enclosing `## N.` group,
  never renumbering existing tasks, and a CLI (`worktrail-normalize-openspec-task-ids`) applies
  the rewrite or checks whether one is needed.
- The dashboard reports a change with untrackable checkboxes as `needs-tasks` with a
  "normalize task ids" next action and an untrackable count — never as `create tasks`,
  `ready-to-implement`, `verify-pending`, `sync-pending`, or `complete`.
- The backlog seeder pre-checks that count: instead of the misleading "create tasks" brief, it
  seeds a distinct planning-only normalization brief under a `<repo>:normalize:<id>` seed key.

## Capabilities

### New Capabilities
- `openspec-task-id-normalization`: `tasks.md` checkboxes without `N.M` ids are detected
  structurally, normalized deterministically and idempotently under their group, and reported
  by the dashboard as a repairable task-id problem rather than a missing task list.

### Modified Capabilities
- `backlog-seeding`: needs-tasks seeding distinguishes untrackable task ids from a genuinely
  missing task list and routes the former to a normalization brief.

## Impact

- `src/worktrail/taskformats/openspec/schema.py`: structured untrackable-checkbox output.
- `src/worktrail/taskformats/openspec/normalize.py` (new): pure normalizer plus CLI.
- `pyproject.toml`: `worktrail-normalize-openspec-task-ids` entry point.
- `src/worktrail/router/dashboard.py`: untrackable detection, next action, and row count.
- `src/worktrail/workqueue/seed_backlog.py`: pre-check, normalization finding kind, brief
  synthesis.
- Tests under `tests/taskformats/openspec/`, `tests/router/`, and `tests/workqueue/`.
- No change to the devkit format. An OpenSpec change whose checkboxes all carry `N.M` ids
  behaves exactly as today.
