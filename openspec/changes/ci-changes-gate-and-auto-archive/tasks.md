## 1. Bookkeeping changes-gate script

- [ ] 1.1 Add `scripts/ci/bookkeeping_gate.sh` (stdlib bash, mirroring
      `scripts/ci/version_bump_check.sh`'s `--github-output` shape): takes
      `--paths-filter-code <true|false>` (the `dorny/paths-filter` `code`
      output — true means a non-doc/openspec/md path changed),
      `--pyproject-diff <path>` (a `git diff -- pyproject.toml` capture,
      empty file if unchanged), and `--github-output <path>`; writes
      `bookkeeping=true|false`. Logic: if `paths-filter-code` is `false`,
      bookkeeping is `true` outright (already docs/openspec/md-only). If
      `true` only because `pyproject.toml` changed and the diff's only
      changed line matches `version = `, bookkeeping is `true`. Otherwise
      `false`. Implements Requirement: Bookkeeping-only diff classification.
- [x] 1.2 Add `scripts/ci/test_bookkeeping_gate.sh` (mirroring
      `test_version_bump_check.sh`'s `run_case`/`assert_kv` pattern): cover
      docs-only, docs+openspec, src-alongside-docs (not bookkeeping),
      pyproject-version-only (bookkeeping), pyproject-with-other-line (not
      bookkeeping), and paths-filter-code=true with pyproject untouched (not
      bookkeeping).

## 2. `changes` and `bookkeeping-bypass` jobs in `ci.yml`

- [ ] 2.1 Add a `changes` job to `ci.yml`: `actions/checkout@v7`, then
      `dorny/paths-filter@v3` with `predicate-quantifier: every`, gated
      `if: github.event_name == 'pull_request'`, defining a `code` filter
      that matches `**` excluding `openspec/**`, `docs/**`, `**/*.md`; add a
      fallback step (`if: github.event_name != 'pull_request'`) that sets
      `code=true` so any non-PR trigger (in particular the existing `push`
      trigger to `main`) always runs the full suite. Capture the
      `pyproject.toml` diff (`git diff <base>...<head> -- pyproject.toml`,
      resolving `<base>` the same way `version_bump_check.yml` already does
      via `github.base_ref`) and feed both into
      `scripts/ci/bookkeeping_gate.sh` to produce a job output
      `bookkeeping`.
      Requirement: "Full suite runs when classification is not bookkeeping-only or is ambiguous" (non-PR fallback branch).
- [ ] 2.2 Gate `lint-test-build`'s test/build steps (or the whole job) on
      `needs.changes.outputs.bookkeeping == 'false'`.
      Requirement: "Full suite skipped for bookkeeping-only diffs" and Requirement: "Full suite runs when classification is not bookkeeping-only or is ambiguous" (both sides of the gate).
- [ ] 2.3 Add a `bookkeeping-bypass` job (`needs: changes`,
      `if: needs.changes.outputs.bookkeeping == 'true'`, `permissions:
      checks: write`) that posts a `success` check named exactly
      `Lint, Test & Build` via `actions/github-script` +
      `github.rest.checks.create`, matching the job `name:` string used
      elsewhere in `ci.yml` and in `.github/rulesets/protect-main.json`.
      Requirement: "Required status check still resolves for bookkeeping-only diffs".

## 3. OpenSpec archive remediation row

- [ ] 3.1 In `src/worktrail/drain/drain.py`, add
      `find_complete_openspec_changes(repos_root, go_repo=None)`: scans
      `dashboard.scan()` results per repo (mirroring
      `find_stale_bookkeeping_specs`'s repo-iteration shape), selecting rows
      with `format == "openspec"` and `stage == "complete"`; returns the
      same `{"repo", "repo_name", "spec_id", "spec_rel"}` finding shape as
      the other finders (resolve `spec_rel` via the existing
      `resolve_spec_rel`).
- [ ] 3.2 Add `archive_openspec_change(finding, agent, timeout, spawner,
      log)`: reusing `close_stale_bookkeeping`'s fix-branch worktree
      lifecycle (`_existing_stale_bookkeeping_pr`-equivalent open-PR check,
      `_reset_stale_bookkeeping_worktree`-equivalent teardown-and-retry,
      `git worktree add -b <branch> <base>`), but the body runs
      `openspec archive -y <change-id>` instead of a status-flip, commits
      whatever it moved/wrote, pushes, and opens the PR via the same
      `_refresh_pr_labels(..., ["go:risk-low"], base)` label-resolution
      path. Title/body: `chore(<change-id>): archive completed change`.
      Raise on `openspec archive` failure or `gh pr create` failure (per
      D2's per-finding isolation, caught by `sweep_remediations`).
- [ ] 3.3 Add a `StageRemediation("openspec_archive", "archive-openspec-change",
      find_complete_openspec_changes, archive_openspec_change)` row to
      `REMEDIATION_TABLE`.
      Requirement: "OpenSpec change archive remediation".
- [ ] 3.4 In `tests/drain/test_drain.py`, cover: an OpenSpec change at
      `stage == "complete"` is found and archived; a devkit spec at
      `stage == "complete"` is NOT selected by the finder (the critical
      scope guard from design.md); no complete OpenSpec changes yields an
      empty `resumed_openspec_archive`; an already-open archive PR is
      detected and returned without re-running `openspec archive`; one
      finding's failure does not block the other `REMEDIATION_TABLE` rows
      (existing per-finding isolation test, extended to the new row).

## 4. Verification

- [ ] 4.1 [e2e] Run `bash scripts/ci/test_bookkeeping_gate.sh`.
- [ ] 4.2 [e2e] Run `PYTHONPATH=src pytest -q tests/drain/test_drain.py`.
- [ ] 4.3 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`.
- [ ] 4.4 [cleanup] Confirm `ci.yml`'s new job names are valid YAML (e.g.
      `actionlint` if available, else a manual read-through) and that
      `bookkeeping-bypass`'s posted check name is byte-identical to
      `"Lint, Test & Build"` in `.github/rulesets/protect-main.json`.
- [ ] 4.5 [cleanup] Run `openspec validate ci-changes-gate-and-auto-archive
      --strict` and confirm the change is structurally valid.
