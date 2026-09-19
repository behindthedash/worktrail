## Why

`automerge_preflight.owner_repo_from_git` hardcodes the `origin` remote
(`src/worktrail/router/automerge_preflight.py:64`). In a fork layout where `origin` is the
read-only upstream and the PR is opened against the fork (aspens: `origin=aspenkit/aspens`,
`fork=behindthedash/aspens`, `remote.pushDefault=fork`), the pre-PR gate reads the *upstream's*
required checks and `allow_auto_merge`, so every PR is labelled `go:no-automerge` even though
the fork has auto-merge enabled and a live ruleset (verified 2026-09-18: the gate reported
`aspenkit/aspens has allow_auto_merge=false` while `behindthedash/aspens` has it `true`). The
push side already honours `remote.pushDefault` (`land_pr._push_target`,
`queue_triage._push_target`), so the branch lands on the fork while the gate inspects the
wrong repo. `check_review_threads` and `pr_labels` reuse the same helper for their bare-number
fallback and inherit the same mismatch. (Work-queue brief
`20260918-193021-preflight-hardcodes-origin-remote`.)

## What Changes

- `owner_repo_from_git` resolves the PR target remote instead of assuming `origin`: it
  honours `git config remote.pushDefault` and falls back to `origin` when it is unset, matching
  the existing push-side rule. An explicit `remote=` argument overrides both.
- Failure text names the remote that was actually consulted rather than "the git origin
  remote".
- The orchestrator's `_preflight_runner` adapter (which injects `-C <repo>` for the helper's
  `git remote get-url` call) also rewrites the new `git config` call so the gate keeps working
  from the verifier's neutral cwd.
- Regression tests cover a fork-layout repo (`origin` upstream, `pushDefault` fork), the
  unset-`pushDefault` fallback, and a `pushDefault` naming a remote that does not exist.

## Capabilities

### New Capabilities
- `automerge-preflight-pr-target-remote`: the automerge preflight gate inspects the repo the PR
  will actually be opened against.

### Modified Capabilities

## Impact

- `src/worktrail/router/automerge_preflight.py` (`owner_repo_from_git`, gate error text).
- `src/worktrail/orchestrator/verify.py` (`_preflight_runner` rewrite of `git config`).
- `tests/router/test_automerge_preflight.py`, `tests/orchestrator/test_verify.py`.
- Callers (`pre_pr_gate`, `check_review_threads`, `pr_labels`, orchestrator `verify`) change
  behaviour only in repos with `remote.pushDefault` set; single-remote repos are unaffected.
- Related but distinct: brief `20260918-183626-land-pr-fork-false-merged` / PR #1265 (land-pr
  fork PR resolution) touches `land_pr.py` only and is not in scope here.
