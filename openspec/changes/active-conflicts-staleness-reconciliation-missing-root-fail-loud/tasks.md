## 1. Fail loud on an unresolved records root

- [x] 1.1 Regression test first, then the fix, in `src/worktrail/router/run_record.py` +
      `tests/router/test_run_record.py`.
      (a) Add failing regressions BEFORE touching source: in `tests/router/test_run_record.py`,
      assert a scan whose records root `<dir>/<repo.name>` does not exist returns empty
      `live`/`stale` **and** a `warnings` entry naming that exact path -- both through
      `_active_conflicts_impl` directly and through the `active-conflicts` CLI -- and that the
      CLI still prints the partitions + warning as JSON while exiting 1. Run them against the
      unmodified source and record the failures (no warning produced; rc 0) as the base
      confirmation of the original defect. Update the two tests that codified the old silent
      behavior -- `test_missing_run_record_directory_returns_empty_list` (CLI path; its local
      `_active_conflicts` helper asserts `rc == 0`) and
      `test_missing_run_record_directory_returns_empty_partitions` (direct impl) -- to the new
      expectation; both are expected to fail before the fix for the same reason.
      (b) In `_active_conflicts()`, turn the whole-scan `if repo_dir.is_dir():` guard
      (line ~1406) into an if/else: on the missing-root branch append a warning naming the
      exact `<dir>/<repo.name>` path (path-prefixed lowercase prose matching
      `_load_lenient`'s tone, e.g. `<repo_dir> does not exist -- no run records were scanned
      for this repo`), leaving both partitions empty. No new return field, no exception, no
      `_is_stale()` change, no caller changes.
      (c) In `cmd_active_conflicts()`, print the JSON as today, then
      `return 0 if repo_dir.is_dir() else 1` -- exit 1 per design D2 (the module's failure
      convention: `cmd_assert_terminal`, `cmd_claim` conflicts, and `main()`'s
      `RunRecordFormatError` handler all return 1; no other nonzero code exists in this
      module), derived from the filesystem predicate, never from matching `warnings`, so a
      malformed-record warning on an existing root keeps exiting 0 (design D3).
      (d) Add the unchanged-existing-root regressions: an existing empty records root stays a
      clean, warning-free, exit-0 result; an existing root with a malformed sibling record
      still exits 0 and its only warning is the malformed-record one.
      (e) Verify: targeted `PYTHONPATH=src python3.14 -m pytest -q
      tests/router/test_run_record.py`, then full `PYTHONPATH=src python3.14 -m pytest -q` and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`,
      `python3.14 scripts/ci/ruff_pinned.py format --check .`, and
      `python3.14 scripts/ci/check_shebang_exec_bits.py`; and re-run the manual repro
      `PYTHONPATH=src python3.14 -m worktrail.router.run_record active-conflicts --dir
      /tmp/definitely-not-there-xyz --repo "$PWD" --specification some-spec`, confirming rc 1
      with the warning visible in the printed JSON.
      (Requirement: Non-terminal run records are partitioned into live and stale)
      files: src/worktrail/router/run_record.py tests/router/test_run_record.py
