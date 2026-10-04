## 1. Enforce the direct-execution interpreter policy

- [x] 1.1 Extend `scripts/ci/check_shebang_exec_bits.py` so its index-based scan also finds every
      executable Python file whose indexed first line is `#!/usr/bin/env python3` and whose source
      contains a PEP 758 unparenthesized multi-exception handler. Report a repository-relative,
      actionable diagnostic directing the author to `python3.14`, retain all EXE001/EXE002 behavior,
      and do not inspect worktree-only content. Extend its temporary-repository tests with generic
      shebang/Pep-758 violations, compliant `python3.14` controls, multiple violations, and an
      index-versus-worktree regression. (Requirements: CI rejects the generic-interpreter PEP 758
      mismatch; Shebang and executable mode agree across platforms)
      files: scripts/ci/check_shebang_exec_bits.py scripts/ci/test_check_shebang_exec_bits.py

## 2. Pin the verified PEP 758 executable baseline

- [x] 2.1 Replace only the first-line `#!/usr/bin/env python3` shebang with
      `#!/usr/bin/env python3.14` in the 38 verified executable PEP 758 files, preserving every
      body line and each Git executable mode: `hooks/suggest_next_step.py`,
      `scripts/ci/ruff_pinned.py`, `src/worktrail/drain/drain.py`,
      `src/worktrail/orchestrator/bootstrap_node_modules.py`,
      `src/worktrail/orchestrator/integrate.py`, `src/worktrail/orchestrator/live.py`,
      `src/worktrail/orchestrator/progress.py`, `src/worktrail/orchestrator/safety_net_report.py`,
      `src/worktrail/orchestrator/spawnlib.py`, `src/worktrail/router/audit_delivery.py`,
      `src/worktrail/router/audit_postmerge.py`, `src/worktrail/router/branch_selfcheck.py`,
      `src/worktrail/router/check_cache_freshness.py`,
      `src/worktrail/router/check_compile_markers.py`,
      `src/worktrail/router/check_deferred_work_handoff.py`,
      `src/worktrail/router/check_durable_artifact_capture_gate.py`,
      `src/worktrail/router/check_repo_freshness.py`, `src/worktrail/router/check_resumable_state.py`,
      `src/worktrail/router/check_review_threads.py`, `src/worktrail/router/check_spec_sync.py`,
      `src/worktrail/router/classifier_coverage.py`, `src/worktrail/router/classify.py`,
      `src/worktrail/router/cluster_detect.py`, `src/worktrail/router/consolidate_cluster.py`,
      `src/worktrail/router/dashboard.py`, `src/worktrail/router/policy_drift_selfcheck.py`,
      `src/worktrail/router/preflight.py`, `src/worktrail/router/quarantine_selfcheck.py`,
      `src/worktrail/router/reconcile_pr_labels.py`, `src/worktrail/router/resolve_repo.py`,
      `src/worktrail/router/run_record.py`, `src/worktrail/router/smoke_flake_selfcheck.py`,
      `src/worktrail/router/sweep_stale_worktrees.py`, `src/worktrail/runtime/detach.py`,
      `src/worktrail/taskformats/devkit/source.py`,
      `src/worktrail/workqueue/backfill_recommended_route.py`,
      `src/worktrail/workqueue/queue_triage.py`, and `src/worktrail/workqueue/work_queue.py`.
      Run the affected existing test modules while making the mechanical change.
      (Requirement: PEP 758 executable files select Python 3.14)
      files: hooks/suggest_next_step.py scripts/ci/ruff_pinned.py src/worktrail/drain/drain.py src/worktrail/orchestrator/bootstrap_node_modules.py src/worktrail/orchestrator/integrate.py src/worktrail/orchestrator/live.py src/worktrail/orchestrator/progress.py src/worktrail/orchestrator/safety_net_report.py src/worktrail/orchestrator/spawnlib.py src/worktrail/router/audit_delivery.py src/worktrail/router/audit_postmerge.py src/worktrail/router/branch_selfcheck.py src/worktrail/router/check_cache_freshness.py src/worktrail/router/check_compile_markers.py src/worktrail/router/check_deferred_work_handoff.py src/worktrail/router/check_durable_artifact_capture_gate.py src/worktrail/router/check_repo_freshness.py src/worktrail/router/check_resumable_state.py src/worktrail/router/check_review_threads.py src/worktrail/router/check_spec_sync.py src/worktrail/router/classifier_coverage.py src/worktrail/router/classify.py src/worktrail/router/cluster_detect.py src/worktrail/router/consolidate_cluster.py src/worktrail/router/dashboard.py src/worktrail/router/policy_drift_selfcheck.py src/worktrail/router/preflight.py src/worktrail/router/quarantine_selfcheck.py src/worktrail/router/reconcile_pr_labels.py src/worktrail/router/resolve_repo.py src/worktrail/router/run_record.py src/worktrail/router/smoke_flake_selfcheck.py src/worktrail/router/sweep_stale_worktrees.py src/worktrail/runtime/detach.py src/worktrail/taskformats/devkit/source.py src/worktrail/workqueue/backfill_recommended_route.py src/worktrail/workqueue/queue_triage.py src/worktrail/workqueue/work_queue.py tests/drain/test_drain.py

## 3. Verification

- [ ] 3.1 [depends: 1.1, 2.1] [e2e] Run `python3 scripts/ci/check_shebang_exec_bits.py`, its focused
      test module, the focused tests for each affected subsystem, `pytest -q`, the orchestrator
      golden regression, pinned Ruff lint and format checks, and `python3 -m build`. Confirm the
      checker reports neither an EXE001/EXE002 violation nor a generic-`python3` PEP 758 violation.
      Run `openspec validate python314-shebang-policy --strict` and `worktrail-compile
      openspec/changes/python314-shebang-policy`.
