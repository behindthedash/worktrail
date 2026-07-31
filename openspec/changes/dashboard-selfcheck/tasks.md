## 1. Implementation

- [ ] 1.1 Add `src/worktrail/router/dashboard_selfcheck.py` implementing
      `check_repo(repo: Path) -> Dict[str, Any]`, `sweep(repos_root: Path) ->
      List[Dict[str, Any]]`, and `main()`, following `policy_selfcheck.py`'s
      shape (module docstring, `--repo`/`--repos-root`/`--json` CLI, exit 0
      clean / 1 flagged). Import `_is_spec_doc` and `_rank` from
      `.dashboard` rather than reimplementing the candidate-classification
      rules; scan only `docs/specs/*/` (skip if the directory doesn't
      exist). A finding fires when 2+ candidates rank 3 (no
      naming-convention signal) and tie, matching `find_spec_file()`'s own
      tie condition exactly.
- [ ] 1.2 Register `worktrail-dashboard-selfcheck =
      "worktrail.router.dashboard_selfcheck:main"` in `pyproject.toml`
      `[project.scripts]`, alongside the existing `worktrail-policy-selfcheck`
      and `worktrail-automerge-selfcheck` entries.

## 2. Tests

- [ ] 2.1 Add `tests/router/test_dashboard_selfcheck.py` covering: a spec
      directory with zero candidates (no finding), one no-signal candidate
      (no finding), a dated or recognized-name candidate present alongside
      no-signal candidates (no finding — matches `find_spec_file()`
      resolving cleanly), and 2+ tied no-signal candidates (finding, naming
      the tied files).
- [ ] 2.2 Add a `sweep()` test with 2+ repos (one clean, one flagged),
      asserting only the flagged repo appears in results and the JSON
      output's `flagged` count matches.
- [ ] 2.3 Add a CLI/exit-code test (`main()` with `--repo`/`--json`) mirroring
      `test_policy_selfcheck.py`'s CLI coverage: exit 0 on a clean repo, exit
      1 with a flagged repo, `--json` output shape.

## 3. Verification

- [ ] 3.1 [cleanup] Run `PYTHONPATH=src pytest -q tests/router/test_dashboard_selfcheck.py`
      and the full `PYTHONPATH=src pytest -q` suite green.
- [ ] 3.2 [cleanup] Run `PYTHONPATH=src pytest -q tests/test_plugin_surface.py` to
      confirm the new console script and any skill/doc cross-references stay
      in lockstep (this change adds no new skill, but the script-registry
      check covers `pyproject.toml` entries).
