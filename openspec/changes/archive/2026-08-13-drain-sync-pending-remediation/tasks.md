## 1. Implementation

- [x] 1.1 Implement the Sync-pending remediation requirement: add `find_sync_pending_specs(repos_root, go_repo)` to `drain.py`, mirroring `find_verify_pending_specs` with the stage filter changed to `sync-pending`.
- [x] 1.2 Add `build_sync_command(agent, repo, spec_id)` to `drain.py`, wrapping the installed `worktrail-skill-dispatch` console script (`--agent`, `--skill opsx:sync`, `--args <spec_id>`, `--cwd <repo>`, `--write`) per design.md.
- [x] 1.3 Add `_run_sync_pending(finding, agent, timeout, spawner, log)` and the public `resume_sync_pending(repos_root, go_repo, agent, timeout, spawner, log)` wrapper, mirroring `_resume_via_full_real`/`resume_verify_pending`'s shape.
- [x] 1.4 Add the `sync_pending` row (`StageRemediation("sync_pending", "resume-sync-pending", find_sync_pending_specs, _run_sync_pending)`) to `REMEDIATION_TABLE`.
- [x] 1.5 Add `summary["resumed_sync_pending"] = resumed.get("sync_pending", [])` to `drain()`'s summary-dict assembly, alongside the existing three keys.

## 2. Tests

- [x] 2.1 `find_sync_pending_specs`: discovers across repos, excludes non-`sync-pending` stages, skips a spec with no resolvable path, respects `go_repo` filter — mirroring `test_find_verify_pending_specs_*`.
- [x] 2.2 `resume_sync_pending`/`_run_sync_pending`: invokes the built `worktrail-skill-dispatch` command once per finding with the correct `--cwd`/`--skill opsx:sync`/`--args <spec_id>`, no-hits is a no-op, one spawn failure does not block the others — mirroring `test_resume_verify_pending_*`.
- [x] 2.3 `build_sync_command`: argv shape assertion (agent, repo, spec_id -> exact command list) for at least the `claude` agent case.
- [x] 2.4 Extend the `REMEDIATION_TABLE`-level tests (pre/post-loop sweep coverage, summary-dict key coverage, generic table-row coverage using `{row.key for row in REMEDIATION_TABLE}`) to account for the fourth row.

## 3. Verification

- [x] 3.1 [cleanup] `PYTHONPATH=src pytest -q` green.
- [x] 3.2 [cleanup] `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` green.
