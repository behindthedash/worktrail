# Tasks

## 1. Merge-base review diff base

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, add a helper next to `_resolve_ref_to_sha`
      that resolves the task worktree's `HEAD` to a SHA and returns
      `git merge-base <start_ref> <that sha>` computed in the canonical repo, returning `None`
      when either git call fails. Use it in `LiveSpawn.__call__` so `ctx["base_commit"]`
      is the merge-base when available and otherwise falls back to the existing
      `_resolve_ref_to_sha(self.repo, start_ref)` value; keep the literal `HEAD` sentinel
      when `self.repo` is `None`. Extend the `base_commit` resolution tests in
      `tests/orchestrator/test_live_extras.py` to cover: a root task reviewed after the
      canonical repo's tip advanced (base_commit equals the fork point, not the new tip), a
      dependent task reviewed after its dependency branch advanced (base_commit equals the
      fork point), the unchanged-base and unchanged-dependency cases still yielding the same
      SHAs as today, the no-common-ancestor fallback to the start ref's SHA, and the
      `repo=None` sentinel fallback.
      (Requirement: The review diff base is the task branch's merge-base with its start ref)
      (Requirement: Merge-base resolution degrades to the previous value)
  files: src/worktrail/orchestrator/live.py tests/orchestrator/test_live_extras.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator/test_live_extras.py`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, and confirm all
      pass. Depends on 1.1.
