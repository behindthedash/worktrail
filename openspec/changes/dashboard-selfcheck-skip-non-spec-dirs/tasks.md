## 1. Scope the selfcheck to spec folders

- [x] 1.1 In `src/worktrail/router/dashboard_selfcheck.py`, extend the existing import from
      `.dashboard` to include `_NON_SPEC_DIRS`, and in `check_repo` skip any `spec_dir` whose
      `name.lower()` is in that set before the candidate glob. Do not restate the names locally
      and do not use `_is_spec_folder` -- its content test would suppress the ambiguous folders
      this detector exists to find; record both points in the module docstring.
      (Requirements: The selfcheck skips known non-spec directories; Real ambiguity findings are
      preserved.)
      In `tests/router/test_dashboard_selfcheck.py`, add coverage over a temporary repo built
      with the file's existing helpers: two untagged candidates under `docs/specs/addenda/`
      yield no finding and `main` exits `0`; the same two candidates under `docs/specs/001-thing/`
      still yield one `ambiguous-spec-doc` finding; a resolvable folder stays clean; and a
      denylisted name is skipped while a non-denylisted folder with no `tasks/`, `changes/` or
      `user-request.md` is still scanned.
      files: src/worktrail/router/dashboard_selfcheck.py, tests/router/test_dashboard_selfcheck.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_dashboard_selfcheck.py`,
      then `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`.
      Run `openspec validate dashboard-selfcheck-skip-non-spec-dirs --strict` and
      `worktrail-compile openspec/changes/dashboard-selfcheck-skip-non-spec-dirs`.
