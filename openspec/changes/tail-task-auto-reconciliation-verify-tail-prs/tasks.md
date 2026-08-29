## 1. `reconcile_unreconciled_tail_evidence` verifier seam

Implements requirement: **Reconciliation PR receives the same CI verification as a group PR**

- [x] 1.1 In `src/worktrail/orchestrator/integrate.py`, add a
      `make_verifier: Optional[Callable[[], "verify.Verifier"]] = None`
      parameter to `reconcile_unreconciled_tail_evidence`.
- [ ] 1.2 Add a module-level default factory (lazily importing `verify`, and
      `live` for `_verifier_role_spawns`, matching this module's existing
      lazy-import pattern) used when `make_verifier` is not provided —
      standalone `Verifier` construction with private locks, for callers
      outside the pipeline scheduler and for tests that don't inject one.
- [ ] 1.3 In the existing per-finding loop, after `integrate_one` and the
      `post_state` read: when `post_state == "OPEN"`, call
      `verifier.verify_one(g, group_branch, ...)` using a freshly-built
      verifier from `make_verifier` (or the default), the same `g` dict
      already constructed, and the `group_branch` recorded for this
      finding's journal entry. Re-read the journal record after `verify_one`
      returns so `reconcile_state`/`pr_url` reflect verify's outcome
      (e.g. a confirmed merge) rather than the pre-verify `OPEN` snapshot.
- [ ] 1.4 Extend the existing per-finding `try/except` so a `verify_one`
      exception is caught the same way an `integrate_one` exception already
      is — recorded as `quarantined` for that finding alone, per design.md's
      per-finding isolation decision.
- [ ] 1.5 Update the function's docstring to describe the new verify step
      and the `make_verifier` parameter.

## 2. Pipeline scheduler wiring

- [ ] 2.1 In `src/worktrail/orchestrator/live.py`'s `_pipeline_scheduler`
      (~line 4973 call site), pass `make_verifier=make_verifier_fn` (the
      function's existing verifier factory, already sharing `iv_lock`/
      `pm_merge_lock`/`pm_cumulative_regression`) to
      `reconcile_unreconciled_tail_evidence`.

## 3. Tests

- [ ] 3.1 In `tests/orchestrator/` (co-locate with existing
      `reconcile_unreconciled_tail_evidence` coverage — see
      `test_live_tail_reconciliation.py`, `test_tail_pr_dedup.py`), add a
      unit test calling `reconcile_unreconciled_tail_evidence` directly with
      a fake `integrate_one` that journals a group to `OPEN` and a fake
      `make_verifier`/`Verifier.verify_one` double, asserting `verify_one` is
      called for that finding's synthetic group.
- [ ] 3.2 Add a test asserting `verify_one` is NOT called for a finding whose
      `post_state` after `integrate_one` is `MERGED` or not `OPEN`
      (quarantined) — no PR to verify.
- [ ] 3.3 Add a test asserting a `verify_one` exception for one finding is
      recorded as `quarantined` for that finding without preventing
      reconciliation of the other findings in the same batch.
- [ ] 3.4 In `test_live_tail_reconciliation.py` (`_pipeline_scheduler`
      integration level), assert the scheduler's call to
      `reconcile_unreconciled_tail_evidence` passes a `make_verifier`
      keyword argument (a callable), confirming the wiring from task 2.1 —
      the regression this whole change exists to prevent: a tail-task PR
      eventually gets a VERIFY step.

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q`.
- [ ] 4.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`.
