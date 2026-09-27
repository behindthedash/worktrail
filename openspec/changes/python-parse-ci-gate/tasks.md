## 1. Parse-gate helper

- [ ] 1.1 Add a side-effect-free `scripts/ci/check_python_parse.py` that enumerates tracked
      `*.py` files, decodes and parses each with the executing interpreter, reports every
      repository-relative `SyntaxError` to stderr, and returns non-zero only after the full scan.
      Add colocated tests for valid files, multiple invalid files, deterministic relative
      diagnostics, tracked-file selection, and no bytecode artifacts. (Requirements: CI parses
      every tracked Python file; Parse failures identify every invalid file)
      files: scripts/ci/check_python_parse.py scripts/ci/test_check_python_parse.py

## 2. Baseline syntax remediation

- [ ] 2.1 Replace every current Python-2-style multi-exception handler in `src/worktrail` with
      the equivalent parenthesized Python 3 form, without changing exception classes, handler
      bodies, or control flow. Add a repository-baseline test that parses the package source and
      asserts no module remains invalid; run the affected module tests to demonstrate unchanged
      behavior. This is one atomic mechanical edit because the modules import one another and
      every repair must land before the new CI gate can pass. (Requirement: The baseline package
      source is parseable)
      files: src/worktrail/addons/aspens.py src/worktrail/conductor/ac_targets.py src/worktrail/conductor/import_deps.py src/worktrail/conductor/parallelism.py src/worktrail/conductor/runplan.py src/worktrail/drain/drain.py src/worktrail/drain/stuck_remediation.py src/worktrail/learning/notes.py src/worktrail/learning/retro.py src/worktrail/onboarding/repo_init.py src/worktrail/orchestrator/agent_capacity.py src/worktrail/orchestrator/bootstrap_node_modules.py src/worktrail/orchestrator/codex_runtime_attestation.py src/worktrail/orchestrator/integrate.py src/worktrail/orchestrator/live.py src/worktrail/orchestrator/progress.py src/worktrail/orchestrator/safety_net_report.py src/worktrail/orchestrator/spawnlib.py src/worktrail/orchestrator/worktree_guard_hook.py src/worktrail/router/audit_delivery.py src/worktrail/router/audit_postmerge.py src/worktrail/router/branch_selfcheck.py src/worktrail/router/check_cache_freshness.py src/worktrail/router/check_compile_markers.py src/worktrail/router/check_deferred_work_handoff.py src/worktrail/router/check_durable_artifact_capture_gate.py src/worktrail/router/check_repo_freshness.py src/worktrail/router/check_resumable_state.py src/worktrail/router/check_review_threads.py src/worktrail/router/check_spec_sync.py src/worktrail/router/classifier_coverage.py src/worktrail/router/classify.py src/worktrail/router/cluster_detect.py src/worktrail/router/consolidate_cluster.py src/worktrail/router/dashboard.py src/worktrail/router/gitnexus_preflight.py src/worktrail/router/land_pr.py src/worktrail/router/policy_drift_selfcheck.py src/worktrail/router/pr_ledger.py src/worktrail/router/preflight.py src/worktrail/router/quarantine_selfcheck.py src/worktrail/router/reconcile_pr_labels.py src/worktrail/router/resolve_repo.py src/worktrail/router/run_record.py src/worktrail/router/smoke_flake_selfcheck.py src/worktrail/router/sweep_stale_worktrees.py src/worktrail/runtime/detach.py src/worktrail/shared/codex_sandbox.py src/worktrail/taskformats/devkit/source.py src/worktrail/workqueue/backfill_recommended_route.py src/worktrail/workqueue/create_handoff.py src/worktrail/workqueue/decisions.py src/worktrail/workqueue/dependency_freshness.py src/worktrail/workqueue/queue_triage.py src/worktrail/workqueue/work_queue.py tests/test_python_parse_baseline.py

## 3. Required-job wiring

- [ ] 3.1 Invoke the parse helper in `Lint, Test & Build` after installation and before pytest,
      using the existing per-step non-bookkeeping condition; extend the workflow test to assert
      the named step's command, ordering, and condition without adding a job-level `if:` or a new
      required context. (Requirement: CI parses every tracked Python file)
      files: .github/workflows/ci.yml tests/test_required_check_jobs.py
      depends: 1.1, 2.1

## 4. Verification

- [ ] 4.1 [e2e] Run `python3 scripts/ci/check_python_parse.py`, the new helper and baseline
      tests, all affected focused tests, `pytest -q`, the orchestrator golden regression, pinned
      Ruff lint and format checks, shebang/exec-bit verification, and `python3 -m build`. Confirm
      the parse helper reports no invalid tracked Python file. (Requirements: CI parses every
      tracked Python file; Parse failures identify every invalid file; The baseline package source
      is parseable)
      depends: 3.1
