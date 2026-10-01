## Why

GitHub made the asynchronous pull-request merge API generally available on 2026-10-01 and
now recommends it for programmatic merges instead of the synchronous REST merge endpoint or
GraphQL merge mutations. The API separates submission from completion: a `PUT` to
`/repos/{owner}/{repo}/pulls/{pull_number}/merge-async` returns a request UUID, and a `GET`
to `/repos/{owner}/{repo}/pulls/{pull_number}/merge-async/{uuid}` reports `pending`,
`merged`, `enqueued`, or `failed`.

Worktrail's orchestrator still performs its confirmed direct merge with `gh pr merge` and
then infers success from that one command returning zero. That path has no durable request
identity, cannot distinguish accepted work from completed work, and is the legacy path GitHub
now recommends replacing. The new endpoint also has a sharp edge for Worktrail: when called
on a stacked pull request it can include open downstack pull requests. Worktrail deliberately
merges dependency groups parent-first and runs a cumulative post-merge smoke gate between
confirmed merges, so blindly adopting the endpoint could skip a safety boundary.

GitHub announcement:
https://github.blog/changelog/2026-10-01-github-async-merge-api-generally-available/

API documentation:
https://docs.github.com/rest/pulls/pulls?apiVersion=2026-03-10#merge-a-pull-request-asynchronously

## What Changes

- Add one shared GitHub async-merge adapter that submits `merge-async` requests with an
  explicit expected head SHA and `bypass_rules=false`, adopts an already-pending matching
  request on HTTP 409, and polls the returned UUID to a typed terminal result.
- Replace the orchestrator's synchronous direct `gh pr merge` attempts with the async
  `direct_merge` action while preserving the configured merge-method fallback behavior.
- Require the PR to target the orchestrator's canonical base before submitting the request,
  so GitHub's stacked-PR behavior cannot merge downstack PRs ahead of Worktrail's cumulative
  post-merge gate.
- Treat `merged` as the only confirmed-merge success. Treat `enqueued` as queued-but-not-
  merged, never run post-merge smoke for it, and never delete its remote branch early.
- Preserve the existing native `gh pr merge --auto` fallback for a branch-protection/human
  gate that must be satisfied later. The async endpoint is not used as a substitute for that
  persistent auto-merge arming behavior in this change.
- Add a regression guard so new Worktrail-owned synchronous direct merge call sites cannot
  silently reappear.

## Capabilities

### New Capabilities
- `github-async-merge-api`: head-bound, non-bypassing async merge submission/polling and the
  orchestrator contract that consumes it safely.

### Modified Capabilities

## Impact

- `src/worktrail/github_async_merge.py`: new shared REST adapter and typed result.
- `src/worktrail/orchestrator/verify.py`: direct merge submission, canonical-base guard,
  method fallback, and confirmed-vs-enqueued handling.
- `tests/`: adapter, orchestrator, stacked-PR safety, cleanup, and regression-guard coverage.
- Agent/domain prose that currently says the orchestrator directly calls `gh pr merge` is
  updated to describe the async request/poll path.
- `.github/workflows/auto-merge.yml` and the repo-init scaffolded auto-merge workflow are not
  changed by this proposal; they own native persistent auto-merge arming, not the confirmed
  direct-merge path this change replaces.
