## 1. Stop releasing a claimed brief whose PR exists

- [ ] 1.1 (Requirements: Merged fold/propose landing tears down its local branch) In
      `src/worktrail/workqueue/queue_triage.py`'s `_worktree_pr_close()`, drop the
      `release(v.brief_id)` call from the post-landing closure-rejection branch
      (`done_res = done(...)` / `if done_res["status"] != "done":` near the end of the
      function). Reaching that branch proves a truthy `pr_url`, because the `refused` and
      empty-`pr_url` outcomes already returned inside the `try`, and the no-PR release is the
      guarded `if not pr_url: release(v.brief_id)` in the cleanup `finally`. The branch SHALL
      return the same error entry minus the release: keep `status: error`, `path`, the
      `done: <status>` error, `branch`, `pr_url`, and `landing`, and report `rolled_back:
      False` as a literal (no `release_res`). Leave a brief comment stating the brief stays
      claimed in `picked/` because a PR exists and the stalled-in-flight resume path closes it
      against that PR. Update the docstring line that promises "rollback (`release()`) on
      `done()` failure" to state the rejected-closure behavior instead. Do NOT call `release()`
      anywhere else, do NOT add a second `if not pr_url:` guard, and do NOT touch
      `_apply_close()`'s release at line 2285 (the `stale-close`/`duplicate-of` path opens no
      PR; its release is deliberate and documented).
      In `tests/workqueue/test_queue_triage.py`'s `TestLandingTeardown`, add a regression case
      for the defect: land a `fold-into-change` verdict with
      `LandOutcome(outcome="landed", final_status="completed_and_merged", pr_url=...)` while
      patching `worktrail.workqueue.queue_triage.done` to return a rejection dict (e.g.
      `{"status": "unverified_reverification_claim", "error": "...", "path": None}`), and
      assert the brief is NOT in `queue/`, IS in `picked/` with
      `read_frontmatter(...)["status"] == "picked"`, and that the entry reports `status:
      error`, `rolled_back: False`, `pr_url`, `branch`, and `landing.final_status:
      completed_and_merged`. Patch `worktrail.workqueue.queue_triage.release` (wrapping the
      real function) and assert it was never called, so the "no release once a PR exists"
      contract is pinned directly and not only via the brief's location. Add the companion
      case in the same class asserting the no-PR `refused` landing still releases the brief
      (back in `queue/` with `status: queued`, absent from `picked/`) and calls `release`
      exactly once -- pinning today's
      `test_refused_without_pr_deletes_branch_and_releases_brief` contract alongside the new
      one. Reuse `_land()`/`_dispatcher()`; never hit the network.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/workqueue/test_queue_triage.py`, then the full `PYTHONPATH=src python3.14 -m pytest
      -q` and `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`. Lint
      through the pinned wrapper: `python3.14 scripts/ci/ruff_pinned.py check .` and
      `... format --check .`. Confirm the fix against the reported repro: with a merged landing
      and a rejecting `done()`, the brief remains in `picked/` and the entry reports
      `rolled_back: false`. Then `openspec validate queue-triage-landed-brief-no-rollback
      --strict` and `worktrail-compile
      openspec/changes/queue-triage-landed-brief-no-rollback`.
      depends: 1.1
