## 1. Implementation

- [x] 1.1 Add `src/worktrail/router/dashboard_selfcheck.py` implementing
      `check_repo(repo: Path) -> Dict[str, Any]`, `sweep(repos_root: Path) ->
      List[Dict[str, Any]]`, and `main()`, following `policy_selfcheck.py`'s
      shape (module docstring, `--repo`/`--repos-root`/`--json` CLI, exit 0
      clean / 1 flagged). Import `_is_spec_doc` and `find_spec_file` from
      `.dashboard` rather than reimplementing the candidate-classification
      rules (`_rank` is a closure nested inside `find_spec_file()`, not a
      module-level name — do not attempt to import it or promote it to
      module scope; that file is out of this task's scope). Scan only
      `docs/specs/*/` (skip if the directory doesn't exist); for each spec
      directory, collect `.md` candidates via `_is_spec_doc`, and if that
      set is non-empty, a finding fires exactly when `find_spec_file(spec_dir)
      is None` — the one condition under which it refuses to guess.
- [x] 1.2 Register `worktrail-dashboard-selfcheck =
      "worktrail.router.dashboard_selfcheck:main"` in `pyproject.toml`
      `[project.scripts]`, alongside the existing `worktrail-policy-selfcheck`
      and `worktrail-automerge-selfcheck` entries.

## 2. Tests

- [x] 2.1 Add `tests/router/test_dashboard_selfcheck.py` covering: a spec
      directory with zero candidates (no finding), one no-signal candidate
      (no finding), a dated or recognized-name candidate present alongside
      no-signal candidates (no finding — matches `find_spec_file()`
      resolving cleanly), and 2+ tied no-signal candidates (finding, naming
      the tied files).
- [x] 2.2 Add a `sweep()` test with 2+ repos (one clean, one flagged),
      asserting only the flagged repo appears in results and the JSON
      output's `flagged` count matches.
- [ ] 2.3 Add a CLI/exit-code test (`main()` with `--repo`/`--json`) covering:
      exit 0 on a clean repo, exit 1 with a flagged repo, `--json` output
      shape. `test_policy_selfcheck.py` has no CLI-level test to mirror (its
      coverage calls `check_repo`/`sweep` directly, never `main()`) — instead
      follow `test_check_spec_collision.py`'s `TestCli` pattern
      (`redirect_stdout` + `main(argv)` + assert on captured stdout/return
      code), the nearest actual precedent in `tests/router/` for testing a
      router self-check's CLI entry point.

## 3. Verification

- [ ] 3.1 [cleanup] Run `PYTHONPATH=src pytest -q tests/router/test_dashboard_selfcheck.py`
      and the full `PYTHONPATH=src pytest -q` suite green.
- [ ] 3.2 [cleanup] Run `PYTHONPATH=src pytest -q tests/test_plugin_surface.py` to
      confirm the new console script and any skill/doc cross-references stay
      in lockstep (this change adds no new skill, but the script-registry
      check covers `pyproject.toml` entries).
