## 1. Add the async merge adapter

- [ ] 1.1 Add `src/worktrail/github_async_merge.py` with a typed result and a runner-injected
      `direct_merge(...)` entry point. Submit
      `PUT repos/{owner}/{repo}/pulls/{pull_number}/merge-async` with API version
      `2026-03-10`, `merge_action=direct_merge`, the requested `merge_method`, mandatory
      expected-head `sha`, and `bypass_rules=false`; handle immediate HTTP 200 terminal
      responses, HTTP 202 request UUIDs, and HTTP 409 existing-request responses. Adopt a 409
      only when its returned head/action/method match the requested operation; otherwise return
      a recoverable conflict. Poll matching UUIDs with
      `GET repos/{owner}/{repo}/pulls/{pull_number}/merge-async/{uuid}` until `merged`,
      `enqueued`, `failed`, or the bounded poll budget is spent; never implicitly re-submit on
      timeout or failure.
      (Requirement: Async merge requests are immutable, head-bound operations)
      (Requirement: Async merge results are polled to a typed terminal outcome)
      Add focused unit tests for 200/202/409, matching-vs-mismatching 409, pending->merged,
      pending->enqueued, pending->failed, malformed responses, and budget exhaustion.
      files: src/worktrail/github_async_merge.py, tests/test_github_async_merge.py

## 2. Move orchestrator direct merges onto the adapter

- [ ] 2.1 In `src/worktrail/orchestrator/verify.py`, include `baseRefName` in the live PR
      status used before merging. After mergeability, CI, and review-thread gates pass, require
      the live PR base to equal `self.base` and pass its current `headRefOid` into the async
      adapter. Replace each non-`--auto` `gh pr merge` attempt (including method fallbacks)
      with `direct_merge(...)`. A stale/moved head returns to verification rather than
      resubmitting against the new head. Preserve the existing method preference/fallback
      order and keep externally armed auto-merge detection ahead of any async request.
      (Requirement: Orchestrator direct merges use the async endpoint after all gates)
      Cover a normal confirmed merge, a still-stacked dependent PR that is not submitted, a
      retargeted dependent that can submit, a moved head, and method fallback.
      files: src/worktrail/orchestrator/verify.py, tests/orchestrator/test_verify.py

- [ ] 2.2 Preserve outcome-dependent safety around the new result: only `merged` enters the
      `merged` accumulator and cumulative post-merge smoke; `enqueued` uses the existing
      queued/armed classification and skips remote branch deletion; `failed` follows the
      existing branch-protection signal path that may arm native `gh pr merge --auto`; a
      confirmed direct async merge relies on `cleanup_group()` for remote branch deletion.
      (Requirement: Async outcomes preserve cleanup and post-merge safety)
      Extend orchestrator tests to prove smoke never runs for `enqueued`, remote deletion is
      skipped for `enqueued`, confirmed `merged` still runs smoke and cleanup, and a
      branch-protection failure can still take the native auto-merge fallback.
      files: src/worktrail/orchestrator/verify.py, tests/orchestrator/test_verify.py

## 3. Prevent the legacy direct path from returning

- [ ] 3.1 Add a source-level regression test that rejects new Worktrail package call sites
      invoking synchronous direct `gh pr merge` while allowing the explicitly retained
      `--auto` arming fallback. Update domain/agent prose that currently describes
      `Verifier.auto_merge()` as unconditionally calling direct `gh pr merge` so the
      documented invariant matches the implementation.
      (Requirement: Synchronous direct merge regressions are rejected)
      files: tests/orchestrator/test_async_merge_callsite_guard.py, .claude/skills/router/skill.md, .claude/skills/worktrail/skill.md

## 4. Verification

- [ ] 4.1 [depends: 1.1, 2.1, 2.2, 3.1] [e2e] Run the focused async-merge and orchestrator
      tests, then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate adopt-github-async-merge-api --strict` and
      `worktrail-compile openspec/changes/adopt-github-async-merge-api`.
