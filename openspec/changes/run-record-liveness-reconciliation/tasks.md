## 1. Persist and evaluate detached-owner liveness (`run-record-liveness-reconciliation`)

- [ ] 1.1 Implement Requirements: Run records bind a detached owner after a
      successful launch; Liveness distinguishes heartbeat freshness from
      detached process state; Orphan sweeping requires confirmed dead-owner
      evidence. In `src/worktrail/router/run_record.py`, add a validated
      detached-owner binding command and persisted optional metadata (detach
      name plus optional state directory); query the existing
      `worktrail.runtime.detach` status contract rather than duplicating PID
      probing. Extend `_run_liveness`/`liveness` JSON additively with detached
      owner state and the design.md D2 reconciliation classes, retaining the
      current heartbeat and dispatch-identity fields. Change
      `_sweep_orphans_repo_dir`/`sweep-orphans` so only a stale-heartbeat
      `confirmed_orphan` reaches `cmd_finish`; retain running owners,
      fresh-after-exit records, and unknown/missing owner evidence with
      distinct JSON summary lists and informative auto-reconciliation notes
      (design.md D1-D3).
      In `tests/router/test_run_record.py`, hermetically stub the detach-status
      seam and cover valid/invalid binding; old records with no metadata;
      stale heartbeat plus `running`, `exited`, `gone`, and `unknown` owner
      states; fresh heartbeat after owner exit; terminal records; and sweep
      behavior for every resulting summary category, including dry-run and no
      write for active/unknown records.
      files: src/worktrail/router/run_record.py, tests/router/test_run_record.py

## 2. Bind the workflow launch handle to its run record (`run-record-liveness-reconciliation`)

- [ ] 2.1 Implement Requirement: Run records bind a detached owner after a
      successful launch. In `skills/worktrail-go/references/subagent-prompts.md`
      at the full-real detached-launch procedure, extract the successful
      launch handle's name and state directory and invoke the new run-record
      binding command against the already-open `$RUN` before the immediate
      status check/Monitor instructions; do not bind a failed or missing
      handle. Update `tests/test_plugin_surface.py` to structurally pin that
      launch-to-bind contract and its ordering, alongside the existing
      native-dispatch run-record threading test.
      files: skills/worktrail-go/references/subagent-prompts.md, tests/test_plugin_surface.py

## 3. Verification

- [ ] 3.1 [e2e] Run `pytest -q tests/router/test_run_record.py
      tests/test_plugin_surface.py`, then `pytest -q`, and confirm the liveness
      decision table and plugin contract pass without machine-wide state.
      depends on 1.1, 2.1. Verification-only; no file changes expected.
- [ ] 3.2 [e2e] Run `openspec validate run-record-liveness-reconciliation
      --strict` and `worktrail-compile
      openspec/changes/run-record-liveness-reconciliation`; confirm both pass.
      depends on 1.1, 2.1. Verification-only; no file changes expected.
