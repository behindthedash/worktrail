## 1. Bounded unknown-owner TTL in `sweep-orphans`

- [x] 1.1 In `src/worktrail/router/run_record.py`: add an optional
      `--unknown-owner-ttl-seconds` int argument (default `None`) to the `sweep-orphans`
      parser and thread it through `cmd_sweep_orphans` into `_sweep_orphans_repo_dir` as
      `unknown_owner_ttl_seconds: int | None`. In the sweep loop, after the
      `active_process` and `fresh` skips, handle `klass == "unknown_owner"`: close it
      (same `cmd_finish` path, same `--status`, appended to `closed`) only when
      `unknown_owner_ttl_seconds is not None`, `liveness["age_seconds"]` is not `None`
      and is greater than the TTL, `record.get("worktree")` is falsy,
      `record.get("files_changed") or []` is empty, and `record.get("pull_request")` is
      falsy; otherwise append to `skipped_unknown_owner` as today. Make the default
      auto-reconciled note for this branch name `reconciliation=unknown_owner`, the
      `age_seconds`, and `unknown_owner_ttl_seconds=<N>`. Keep every other class's
      handling unchanged. Update the module docstring's `sweep-orphans` entry (the
      "never closed automatically" sentence) to describe the opt-in bounded policy.
      (Requirement: Orphan sweeping requires confirmed dead-owner evidence.)
      In `tests/router/test_run_record.py`, extend the `_sweep_orphans` helper to pass
      `--unknown-owner-ttl-seconds` when an `unknown_owner_ttl_seconds` override is given,
      then add tests for the five new spec scenarios: an unbound record backdated past the
      TTL with no work product is closed with the requested status and a note containing
      `reconciliation=unknown_owner` and the TTL; one younger than the TTL stays
      `skipped_unknown_owner`; one past the TTL with a `worktree`, with a non-empty
      `files_changed`, and with a `pull_request` each stay `skipped_unknown_owner`; a
      record with no `updated_at` stays skipped even with the flag; the same past-TTL
      record without the flag stays skipped; and `--dry-run` lists it under `closed`
      without writing `final_status`.
      files: src/worktrail/router/run_record.py, tests/router/test_run_record.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_run_record.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate run-record-sweep-unknown-owner-ttl --strict` and
      `worktrail-compile openspec/changes/run-record-sweep-unknown-owner-ttl`.
