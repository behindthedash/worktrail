## 1. Quarantine detector module

- [ ] 1.1 Create `src/worktrail/router/quarantine_selfcheck.py` implementing
      `check_repo(repo: Path) -> Dict[str, Any]`: glob `<repo>-worktrees/run-*.json`
      (sibling to the convention `dashboard.py`'s `_journal_verify_pending()` uses),
      parse each journal's `groups` dict, and emit one finding per entry with
      `state == "QUARANTINED"` carrying spec id (from filename), group name,
      `pr_url`, and `age_days` (from the journal file's mtime). No network calls
      (local file inspection only, matching `check_repo_freshness.py`'s default
      posture).
- [ ] 1.2 In the same file, add `sweep(repos_root: Path) -> List[Dict[str, Any]]`
      and a `main()` CLI (`--repo`, `--repos-root`, `--json`), matching
      `src/worktrail/router/automerge_selfcheck.py`'s exit-0-clean /
      exit-1-flagged convention exactly.
- [ ] 1.3 Add the `worktrail-quarantine-selfcheck` console-script entry point to
      `pyproject.toml`'s `[project.scripts]`, alongside the existing
      `worktrail-automerge-selfcheck` / `worktrail-policy-drift-selfcheck` entries.
- [ ] 1.4 Add `tests/router/test_quarantine_selfcheck.py` covering: no
      `run-*.json` files → empty findings; a journal with no `QUARANTINED` group
      → empty findings; a journal with one `QUARANTINED` group → one finding
      with correct spec id/group/pr_url/age_days; `sweep()` over multiple repos
      flags only the ones with findings; CLI `--json` output shape and exit
      codes (0 clean, 1 flagged).

## 2. Dashboard wiring

- [ ] 2.1 In `src/worktrail/router/dashboard.py`, import
      `quarantine_selfcheck.check_repo as _quarantine_check_repo` alongside the
      existing `policy_drift_selfcheck`/`automerge_selfcheck` imports.
- [ ] 2.2 In `scan_repos()`, call `_quarantine_check_repo(repo)["findings"]` per
      candidate repo and attach the result as `quarantine_findings` on both the
      per-repo `repo_info` dict and the returned row dict, following the exact
      pattern already used for `policy_findings`/`automerge_findings`/`drift_findings`.
- [ ] 2.3 In `render_dashboard()`, build a `quarantine_flags` list from each repo
      row's `quarantine_findings` and render it as one capped summary line
      (repo, spec id, group, age_days; cap at 4 with a "+N more" suffix; a
      "→ review" nudge), inserted alongside the existing `policy_flags`/
      `automerge_flags`/`drift_flags` lines. An all-empty `quarantine_findings`
      across every repo must leave rendered output byte-for-byte unchanged.
- [ ] 2.4 Extend `tests/router/test_dashboard.py`: `scan_repos()` returns
      `quarantine_findings` per repo row (empty when clean, populated when a
      journal has a `QUARANTINED` group); `render_dashboard()` renders the new
      summary line only when at least one repo has findings, and omits it
      entirely otherwise (regression-protects existing golden output).

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; both must be green.
- [ ] 3.2 [e2e] Run `worktrail-quarantine-selfcheck --repo <this-checkout>`
      manually and confirm it exits 0 with no findings against this repo's own
      `run-*.json` state (or correctly reports any real quarantined group if
      one exists), as an end-to-end sanity check beyond unit tests.
