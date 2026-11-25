## 1. Detect and normalize untrackable task ids

- [x] 1.1 Record untrackable top-level checkboxes in `ParsedTasks` and add the normalizer plus
      its CLI.
      Extend `parse_tasks_md` so `ParsedTasks` carries an `untrackable` list of line-number and
      text entries for every top-level checkbox that does not match `TASK_RE`; keep the
      existing warning and keep indented checkboxes tolerated as sub-items. Add
      `src/worktrail/taskformats/openspec/normalize.py` with a pure
      `normalize_untrackable_task_ids(text)` that assigns `N.M` ids under each `## N.` heading
      from that group's highest existing suffix plus one, never renumbers existing ids, leaves
      pre-heading checkboxes unchanged and reported, preserves every byte outside the inserted
      id, and is idempotent. Add its `main()` accepting a change directory or `tasks.md` path,
      rewriting atomically only on change by default and supporting `--check` with no writes.
      Register `worktrail-normalize-openspec-task-ids` in `pyproject.toml`'s
      `[project.scripts]`. (Requirement: Untrackable top-level checkboxes are detected
      structurally) (Requirement: Normalization assigns N.M ids under the enclosing group)
      (Requirement: Checkboxes outside a group are reported, never guessed) (Requirement: A CLI
      applies and checks normalization)
      In `tests/taskformats/openspec/test_openspec_source.py` cover the all-untrackable, mixed,
      and indented-checkbox cases; in the new
      `tests/taskformats/openspec/test_openspec_normalize.py` cover group-scoped assignment,
      non-renumbering, checked-state and continuation preservation, ungrouped reporting,
      idempotence, and the CLI's default, `--check`, and exit-status behavior.
      files: src/worktrail/taskformats/openspec/schema.py src/worktrail/taskformats/openspec/normalize.py pyproject.toml tests/taskformats/openspec/test_openspec_source.py tests/taskformats/openspec/test_openspec_normalize.py

- [x] 1.2 Report untrackable ids in the dashboard and pre-check them in the backlog seeder.
      Extend `_safe_detect_openspec` so it inspects `tasks.md` for untrackable top-level
      checkboxes before stage selection and, when any exist, reports stage `needs-tasks`, a
      next action naming task-id normalization with the count, and an
      `untrackable_task_checkboxes` row field; that classification takes precedence over
      `create tasks`, `ready-to-implement`, `verify-pending`, `sync-pending`, and `complete`,
      while changes without untrackable checkboxes keep their existing behavior. Extend
      `find_needs_tasks_specs` and `seed_backlog` to classify a row with a non-zero count as a
      `normalize-tasks` finding and seed a distinct planning-only Route C brief under the
      `<repo>:normalize:<id>` seed key whose focus names
      `worktrail-normalize-openspec-task-ids` and forbids spec-to-tasks; order those candidates
      first and keep the sweep deterministic and capped. (Requirement: Changes with untrackable
      ids are not reported ready or complete) (Requirement: needs-tasks specs are seeded as
      planning-only Route C briefs) (Requirement: Untrackable OpenSpec task ids are seeded as a
      normalization brief) (Requirement: Seeding is bounded, deterministic, and loudly capped)
      In `tests/router/test_dashboard.py` cover an all-untrackable change, a mixed change, a
      completed change with one untrackable checkbox, and an unchanged clean change; in
      `tests/workqueue/test_seed_backlog.py` cover the normalization brief's route, intent,
      target, distinct seed key, no-reseed behavior, and first-in-order cap behavior.
      depends: 1.1
      files: src/worktrail/router/dashboard.py src/worktrail/workqueue/seed_backlog.py tests/router/test_dashboard.py tests/workqueue/test_seed_backlog.py

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/taskformats/openspec tests/router/test_dashboard.py
      tests/workqueue/test_seed_backlog.py`, then the full suite and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate normalize-openspec-untrackable-task-ids --strict` and
      `worktrail-compile openspec/changes/normalize-openspec-untrackable-task-ids`. Fixture a
      change whose `tasks.md` has id-less checkboxes, run
      `worktrail-normalize-openspec-task-ids --check` against it, and confirm it reports the
      lines and exits non-zero without writing.
