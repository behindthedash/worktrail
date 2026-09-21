## 1. Gate the BLOCKED classification on required-context reporting

- [ ] 1.1 In `src/worktrail/router/land_pr.py`: add a module-level
      `_required_contexts_reported(status, required_contexts) -> bool` that returns `True` when
      `required_contexts` is `None` or empty, and otherwise `True` only when every required
      context appears in `status["statusCheckRollup"]` (matching an entry's `name` or
      `context`, exactly as `_merge_state_guard` already normalises them) with a terminal
      conclusion — a `conclusion` that is set, or a `state` that is not a pending/queued/
      in-progress value — alongside a helper returning the outstanding context names. Wrap the
      `mergeStateStatus == "BLOCKED"` branch (`land_pr.py:1826`) in a bounded re-poll: while
      the state is BLOCKED and `_required_contexts_reported` is `False`, re-enter
      `_merge_state_guard` up to a new module-level budget constant (sleeping the existing
      poll interval between attempts); a state that is no longer BLOCKED falls through to the
      existing flow unchanged, coverage completing proceeds to the existing
      `blocked_product_decision` path unchanged, and a spent budget returns
      `LandOutcome(outcome="ceiling", final_status="failed_recoverable", ...)` whose
      `merge_result` names the outstanding required contexts, without completing the run
      record as `blocked_product_decision`.
      (Requirement: Merge-state block is classified only on required-context reporting.)
      In a new `tests/router/test_land_pr_blocked_required_contexts.py` using `FakeRun` from
      `tests/router/test_land_pr.py`, cover every scenario of that requirement: a pending
      required context re-polls; an absent required context re-polls; coverage completing on a
      later poll classifies `blocked_product_decision`; the merge state clearing on a later
      poll continues the normal flow; a spent budget returns ceiling/`failed_recoverable`
      naming the outstanding contexts and leaves the record uncompleted as
      `blocked_product_decision`; a failing-but-terminal required context classifies
      immediately; `None` contexts and `[]` contexts both classify immediately as before.
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr_blocked_required_contexts.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate land-pr-merge-state-guard-await-required-contexts --strict` and
      `worktrail-compile openspec/changes/land-pr-merge-state-guard-await-required-contexts`.
