## 1. Provides fallback in the dashboard task loader (`devkit-task-file-scope-resolution`)

- [x] 1.1 In `src/worktrail/router/dashboard.py`, add a helper that slices a
      `TASK-*.md`'s leading `---` frontmatter block, `yaml.safe_load`s it, and returns
      the ordered, de-duplicated `file` strings from its `provides:` list, returning
      `[]` on `yaml.YAMLError`, a non-list `provides`, or entries that are not maps
      with a string `file`. In `_load_tasks`, use that helper to fill the row's
      `files` only when `fm.get("files", [])` is empty; a non-empty `files:` stays
      authoritative. Update the `_load_tasks` docstring to name the fallback.
      (Requirements: Task file scope falls back to provides entries; An explicit
      files list takes precedence over provides; Malformed provides degrades to
      empty scope.)
      In `tests/router/test_dashboard.py`, extend the existing real-git
      stale-bookkeeping test class with a `provides:`-only task writer and cover: a
      `kind: e2e` provides-only task whose output file shipped flips the spec to
      `stale-bookkeeping` with the id in `stale_task_ids`; a provides-only pending
      impl task with two shipped outputs is stale; one unshipped output keeps the
      spec at `ready-to-implement`; `files:` present alongside `provides:` probes only
      `files:`; `provides: done` and an entry without `file` degrade to the current
      empty-scope behaviour without raising.
      files: src/worktrail/router/dashboard.py, tests/router/test_dashboard.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_dashboard.py
      tests/router/test_dashboard_selfcheck.py tests/router/test_spec_sync_sweep_stale_bookkeeping_check.py`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate dashboard-stale-probe-read-provides-frontmatter --strict` and
      `worktrail-compile openspec/changes/dashboard-stale-probe-read-provides-frontmatter`.
