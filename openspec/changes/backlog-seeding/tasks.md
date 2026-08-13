## 1. Seeder module

- [x] 1.1 Add `src/worktrail/workqueue/seed_backlog.py`: `find_needs_tasks_specs` (dashboard
      scan, `needs-tasks` stage only), `find_epic_gaps` (feature headings vs citing specs,
      terminal-status and unparseable handling), `existing_seed_keys` dedup,
      `seed_backlog()` orchestration (deterministic order, cap with logged deferral,
      per-candidate error isolation), and a `main()` CLI (`--repos-root`, `--repo`,
      `--queue-dir`, `--max-seeds`, `--dry-run`, `--json`).
      Implements "needs-tasks specs are seeded as planning-only Route C briefs".
      Implements "Epics with unspecced features are seeded by citation gap".
      Implements "Seed keys are deduplicated against the whole queue and progress-keyed".
      Implements "Seeding is bounded, deterministic, and loudly capped".
- [x] 1.2 Extend `create_handoff()` with an optional `seeded_from` parameter that stamps a
      `seeded-from:` frontmatter key on the created brief.
- [x] 1.3 Register the `worktrail-seed-backlog` console script in `pyproject.toml`.

## 2. Drain integration

- [x] 2.1 In `src/worktrail/drain/drain.py`, run `seed_backlog()` after the pre-loop remediation
      sweep (guarded on `repos_root`, `not dry_run`, and a new `DrainConfig.seed_backlog`
      flag / `--no-seed-backlog` CLI opt-out), catching and logging any exception, and record
      the summary under `seeded_backlog`.
      Implements "The drain tops the queue up before draining, best-effort".

## 3. Tests

- [x] 3.1 `tests/workqueue/test_seed_backlog.py`: needs-tasks seeding shape,
      needs-clarification exclusion, tasked-spec exclusion, epic gap seeding, fully-cited and
      terminal-status epics, unparseable epics, non-epic files, whole-queue dedup (including a
      done brief), epic progress re-arming, cap + deterministic order, dry-run, `--repo`
      restriction, CLI JSON and error paths.
- [x] 3.2 `tests/drain/test_drain.py`: seeding runs pre-loop and appears in the summary,
      `--no-seed-backlog` opt-out, seeding failure never aborts the drain, dry-run never seeds.
- [x] 3.3 `tests/conftest.py`: suite-wide `WORK_QUEUE_DIR` isolation so no test can fall back to
      the operator's real queue.
