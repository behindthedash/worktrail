## 1. Null-safe scope-review append (`run-record`)

- [x] 1.1 In `src/worktrail/router/run_record.py`, in the `scope-review` handler, replace
      `record.setdefault("scope_review", []).append(...)` with the module's existing
      null-tolerant idiom: read `record.get("scope_review") or []`, append the
      `"<status> | <item> | <detail>"` entry, and assign the list back to
      `record["scope_review"]` before `_save()`. No other behaviour changes.
      (Requirement: Scope-review append tolerates a null list field.)
      In `tests/router/test_run_record.py`, add tests for the three spec scenarios: a
      record written with `scope_review: null` appends without raising and saves a list;
      a record with no key still works; an existing entry is preserved ahead of the new one.
      files: src/worktrail/router/run_record.py, tests/router/test_run_record.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_run_record.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate run-record-null-list-field-guard --strict` and
      `worktrail-compile openspec/changes/run-record-null-list-field-guard`.
