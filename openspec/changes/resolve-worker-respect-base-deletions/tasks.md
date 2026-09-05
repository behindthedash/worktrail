## 1. Resolve-worker prompt: don't resurrect base deletions

- [ ] 1.1 In `src/worktrail/orchestrator/dispatch.py`, in `build_group_prompt()`'s `ROLE_RESOLVE`
      branch (the `action` list around lines 818-832), add a new bullet after step 2's "Resolve
      every conflict MINIMALLY, preserving the intent of BOTH sides" line, stating explicitly
      that a path the base branch DELETED stays deleted — the worker must not restore or recreate
      it to "preserve both sides," especially a path outside the group's own declared task scope
      (e.g. another OpenSpec/devkit change directory). In `tests/orchestrator/test_dispatch_extras.py`,
      add a test alongside the existing `build_group_prompt(dispatch.ROLE_RESOLVE, ...)` coverage
      asserting the rendered prompt contains this base-deletion instruction (grep for a
      distinguishing phrase, e.g. "base deleted" or "stays deleted"). (Requirement: Resolve-worker
      prompt forbids resurrecting base deletions)

## 2. Track a confirmed forbidden-path violation per group

- [ ] 2.1 In `src/worktrail/orchestrator/verify.py`, add `self._forbidden_path_violations:
      dict[str, str] = {}` next to the existing `self._self_merge_violations: dict[str, str] = {}`
      init (line 406). In `_spawn_group_worker()` (around lines 894-900), when
      `_forbidden_paths_touched()` returns a non-empty list, in addition to the existing log line
      and `return False`, set `self._forbidden_path_violations[group["name"]] = <a message naming
      role and the touched paths>` before returning. In `tests/orchestrator/test_verify.py`, add a
      test that spawns a resolve worker whose reported diff touches a forbidden path and asserts
      `v._forbidden_path_violations` gains an entry for the group naming the touched path(s) — model
      it on the existing `test_forbidden_workflow_edit_fails_strike_despite_reported_success` (around
      line 2509), extended to check the new dict instead of only the return value and log.
      (Requirement: Confirmed forbidden-path violation is tracked per group)

## 3. Gate the live-merge recheck off a confirmed forbidden-path violation

- [ ] 3.1 In `src/worktrail/orchestrator/verify.py`, thread a new `forbidden_path_violations:
      dict[str, str]` parameter through `verify_one()` (alongside the existing `self_merged`
      parameter) and `run_all()` (alongside a new `forbidden_path_violations: dict[str, str] = {}`
      local next to the existing `self_merged: dict[str, str] = {}` at line 1853, passed through
      both `self.verify_one(...)` call sites — the single-group call around line 1896 and the
      `ThreadPoolExecutor` submit around line 1914). In `verify_one()`'s `if not ok:` block (lines
      1792-1821), read `forbidden_violation = self._forbidden_path_violations.get(name)` alongside
      the existing `violation = self._self_merge_violations.get(name)`, add `and not
      forbidden_violation` to the `_recheck_merged_before_quarantine` guard condition (line
      1795-1798), and add a branch (mirroring the existing `elif violation and self_merged is not
      None:` at lines 1811/1817) that records `forbidden_path_violations[name] =
      forbidden_violation` and logs a distinct "FORBIDDEN-PATH VIOLATION" line instead of falling
      through to `quarantined[name] = reason`. In `run_all()`, also include the new dict in the
      final summary log block (mirroring the existing `if self_merged:` block around lines
      1936-1939) and in the returned dict (mirroring `"self_merged": self_merged,` at line 1958)
      under the key `"forbidden_path_violations"`. Depends on 2.1. In
      `tests/orchestrator/test_verify.py`, add an end-to-end `run_all()` test modeled on
      `test_resolve_worker_self_merge_surfaces_distinctly_not_quarantined` (line 2777): a resolve
      worker's pushed commit touches a forbidden path and reports `status: success`, the PR is
      then observed `MERGED` on the live recheck poll, and the assertions are `res["merged"] ==
      []`, `res["quarantined"] == {}`, `name in res["forbidden_path_violations"]`. Also add a
      negative-regression test confirming a group with no forbidden-path violation still merges
      normally through the existing recheck path (unchanged behavior). (Requirements: Confirmed
      forbidden-path violation surfaces as its own outcome, never silently merged; Confirmed
      forbidden-path violation [`quarantine-live-merge-recheck`]; No forbidden-path violation
      recorded)

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends on
      1.1, 2.1, 3.1.
