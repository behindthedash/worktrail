## 1. Guard implementation

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, add a helper that, given the
      journal `entries` and the currently loaded `tasks` list, returns the set
      of foreign task ids (non-event entries whose `task` id has no match in
      `tasks`).
- [ ] 1.2 In `_full_real_inner`'s resume block (the
      `if resume and Path(journal_path).exists():` branch), call the helper
      immediately after `reconcile_from_journal()` returns and, if any foreign
      ids are found, raise a `RuntimeError` naming every foreign task id, the
      journal path, and the requested `--spec` path — before
      `validate_task_metadata()` runs.
- [ ] 1.3 Confirm the error message tells the operator to re-run with
      `--fresh` to discard the journal and start clean.

## 2. Tests

- [ ] 2.1 Unit test: a journal with entries whose task ids all match the
      current task set resumes exactly as before (no new error).
- [ ] 2.2 Unit test: a journal with one or more entries whose task ids are
      absent from the current task set raises the foreign-journal error and
      reconciles nothing from that journal.
- [ ] 2.3 Unit test: a journal with a mix of matching and foreign entries
      raises the same error (partial collision is not treated as safe).
- [ ] 2.4 Unit test: `event`-only journal markers (e.g.
      `dependency_file_drift`) are never considered when computing foreign
      ids.
- [ ] 2.5 Unit test: after a foreign-journal error, re-running with
      `resume=False` (`--fresh`) discards the journal and starts cleanly with
      no error.

## 3. Validation

- [ ] 3.1 Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
      green.
