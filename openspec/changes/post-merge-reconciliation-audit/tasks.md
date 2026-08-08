## 1. Core audit module

- [ ] 1.1 Create `src/worktrail/router/audit_postmerge.py`: import `discover_managed_repos` from `reconcile_pr_labels.py` and `classify_checks` from `orchestrator/verify.py` (no duplication of either).
- [ ] 1.2 Implement per-repo marker read/write against `~/.go/postmerge-audit-state/<repo-name>.json` (`{"last_swept_at": ISO8601}`), overridable via `--state-dir` / `GO_POSTMERGE_AUDIT_STATE`; missing/corrupt marker degrades to the bounded first-run lookback window.
- [ ] 1.3 Implement merged-PR listing via `gh pr list --state merged --search "merged:>=<marker-or-lookback>"` and per-PR `gh pr view --json url,number,mergedAt,statusCheckRollup`, capped at `--max-prs` (default 50) per repo per sweep; `gh` failure/absence fails open (report `error`, leave marker unchanged) matching `reconcile_pr_labels.py`'s `_open_prs()` posture.
- [ ] 1.4 Classify each fetched rollup with `classify_checks()`; record flagged PRs (repo, url, failing check names, merged-at) into the repo's state file; advance the marker only past PRs actually checked this sweep.
- [ ] 1.5 Implement `dashboard_snapshot(state_dir)`: pure read of persisted state files, returns a summary dict — no `gh` calls, no side effects.
- [ ] 1.6 Add a `main(argv)` CLI entrypoint to `src/worktrail/router/audit_postmerge.py` (argparse, `--repo`/`--repos-root`/`--state-dir`/`--lookback-days`/`--max-prs`/`--json` flags mirroring `reconcile_pr_labels.py`'s CLI shape) plus the matching `worktrail-audit-postmerge` console script entry in `pyproject.toml` `[project.scripts]` pointing at it — both files together, since the entry point is meaningless without the function it points to.

## 2. Tests for the core module

- [ ] 2.1 Unit tests for marker persistence: first-run lookback default, marker advances on success, marker unchanged on `gh` failure, corrupt/missing marker degrades to first-run window (mirrors `tests/router/test_reconcile_pr_labels.py` structure).
- [ ] 2.2 Unit tests for `classify_checks()` reuse: a merged PR with a failing required check is flagged; a merged PR with all-green checks is not; an informational/non-required check failure is not flagged (matches `classify_checks()`'s own existing informational-check exclusion).
- [ ] 2.3 Unit tests for `--max-prs` capping: a repo with more candidate merged PRs than the cap only processes the cap's worth, and the marker only advances past what was actually processed.
- [ ] 2.4 Unit tests for `dashboard_snapshot()`: empty state directory returns an empty summary; a state file with flagged PRs returns them; a state file with only clean sweeps returns empty.

## 3. Dashboard integration

- [ ] 3.1 Wire `router/dashboard.py` to import `audit_postmerge.dashboard_snapshot` and fold its result into a new additive `postmerge_check_failures` field in the dashboard's JSON output, following the existing `capacity`/`agent_capacity.gate_snapshot()` call pattern.
- [ ] 3.2 Add a rendered-text summary line (only emitted when `postmerge_check_failures` is non-empty) alongside the existing `capacity`-gated render lines in `dashboard.py`.
- [ ] 3.3 Add a `--postmerge-audit-state` CLI flag to `worktrail-dashboard` mirroring the existing `--capacity-cache` flag, defaulting to `GO_POSTMERGE_AUDIT_STATE` / `~/.go/postmerge-audit-state`.
- [ ] 3.4 Tests: dashboard JSON output is unchanged in shape/content for existing fields when no postmerge audit state exists; `postmerge_check_failures` appears correctly when state is present; rendered text includes the summary line only when non-empty.

## 4. Reference scheduled deployment (worktrail's own repo)

- [ ] 4.1 Add `.github/workflows/postmerge-reconciliation-audit.yml`: scheduled workflow calling `worktrail-audit-postmerge --repo "$PWD" --json`, mirroring `rulesets_drift_guard.yml`'s existing scheduled-workflow pattern in this repo.

## 5. Verification

- [ ] 5.1 [e2e] `PYTHONPATH=src pytest -q` green, including new tests from sections 2 and 3.
- [ ] 5.2 [e2e] `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` green (golden record/replay regression).
- [ ] 5.3 [e2e] Manual smoke: run `worktrail-audit-postmerge --repo "$PWD" --json` against the worktrail repo itself (real `gh` call) and confirm it completes without error and reports no false positives against already-known-good recently-merged PRs.
