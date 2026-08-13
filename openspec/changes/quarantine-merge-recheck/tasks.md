## 1. Live merge recheck helper

- [x] 1.1 In `src/worktrail/orchestrator/verify.py`, add
      `Verifier._recheck_merged_before_quarantine(self, group: Dict[str, Any], gb: str) -> bool`:
      call `self.pr_status(gb)`; if it returns `None`, return `False`. If `state` is `MERGED`,
      log a one-line note naming the group and return `True`. Else if `autoMergeRequest` is
      truthy, log a one-line note that a bounded wait is starting, call
      `self._wait_for_external_merge(group, gb)`, and return its `ok` value. Otherwise return
      `False`. Never call `gh pr merge`, `auto_merge`, or any other merge-arming action.
      Implements requirement "Live merge recheck before finalizing an ordinary quarantine verdict".
      Implements requirement "Bounded wait for an externally-armed auto-merge before finalizing quarantine".
      Implements requirement "Recheck is passive and never arms or attempts its own merge".

## 2. Wire the recheck into verify_one's quarantine path

- [x] 2.1 In `Verifier.verify_one`, in the existing `if not ok:` block, compute `violation` and
      `is_regression` exactly as today, then — only when neither is true — call
      `self._recheck_merged_before_quarantine(group, group_branch)`. On `True`: append `name` to
      `merged` (under the existing `lock`), log a one-line note that the group merged on final
      recheck despite the earlier failure `reason`, call
      `self.cleanup_group(group, group_branch, delivered)` (default
      `skip_remote_branch_delete=False`, matching the already-merged path in `auto_merge`), and
      `return` — skipping the existing quarantine/self-merge/regression accumulator writes below
      it. On `False`, fall through to the existing accumulator-write logic unchanged.
      Gating on `not violation and not is_regression` implements requirement "Self-merge violations and post-merge regressions are unaffected".

## 3. Tests

- [x] 3.1 In `tests/orchestrator/test_verify.py`, add a unit test for
      `_recheck_merged_before_quarantine` covering three cases directly (no `run_all` needed):
      `pr_status` returns `state: MERGED` → `True`; `pr_status` returns an armed
      `autoMergeRequest` and a subsequent poll flips to `MERGED` → `True`; `pr_status` returns
      neither merged nor armed → `False`.
- [x] 3.2 Add an integration-level test through `run_all`/`verify_one` reproducing the PR #339
      shape: `wait_and_fix_ci` exhausts its 3-strike budget (same setup as
      `CiFixExhaustionPath.test_three_strikes_quarantines_and_keeps_worktree`), but the PR's live
      state has flipped to `MERGED` by the time the final recheck runs. Assert the group lands in
      `res["merged"]`, not `res["quarantined"]`, and that `cleanup_group`'s effects ran (task
      worktree/branch removed — mirrors `CleanGreenPath`'s assertions).
- [x] 3.3 Confirmed a confirmed self-merge violation (existing
      `test_resolve_worker_self_merge_surfaces_distinctly_not_quarantined`, whose final PR view is
      already `state=MERGED`) is unaffected by this change — annotated that test to make explicit
      it also guards the new recheck's gating; the violation still lands in `self_merged`, not
      `merged`. A separate near-duplicate test was not added.
- [x] 3.4 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`; both must be green.
