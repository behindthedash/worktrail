## Context

GitHub's async merge API has two endpoints relevant here:

- `PUT /repos/{owner}/{repo}/pulls/{pull_number}/merge-async` accepts a merge request.
  A new request returns HTTP 202 and a UUID; HTTP 200 can report a PR already `merged` or
  already `enqueued`; HTTP 409 reports an existing pending request for that PR.
- `GET /repos/{owner}/{repo}/pulls/{pull_number}/merge-async/{uuid}` reports the current
  result. `pending` is non-terminal; terminal results are `merged`, `enqueued`, and `failed`.

The request accepts an expected head `sha`, a `merge_method` for `direct_merge`, a
`merge_action`, and `bypass_rules`. GitHub documents that a stacked-PR merge request includes
open downstack PRs. It also documents that an `enqueued` result is final for the request but
does not mean the PR itself has merged.

Worktrail's orchestrator already has the safety structure we need around the transport:
`ensure_mergeable` -> CI -> review-thread resolution -> serialized merge -> cumulative
post-merge smoke -> cleanup. The change is therefore intentionally a transport/state-machine
replacement, not a rewrite of that sequencing.

## Goals / Non-Goals

- Goal: replace every Worktrail-owned synchronous *direct* PR merge in the orchestrator with
  GitHub's recommended async merge API.
- Goal: make accepted-vs-completed merge state explicit and head-safe.
- Goal: preserve dependency ordering, cumulative post-merge smoke, method fallback, and
  branch cleanup semantics.
- Non-goal: replace the repo's native `--auto` arming workflow. That workflow intentionally
  arms a merge that may wait on future policy/human state; this change targets direct merges
  attempted only after Worktrail's own gates are green.
- Non-goal: opt repositories into GitHub merge queues or add a new merge-queue policy key.
- Non-goal: exploit the endpoint's stacked-PR co-merge behavior. Worktrail must continue to
  validate and land dependency groups one confirmed merge at a time.
- Non-goal: bypass branch protection or repository rules.

## Decisions

**D1 - One adapter owns request/response semantics.** Add
`src/worktrail/github_async_merge.py` rather than embedding `gh api` parsing in
`Verifier.auto_merge()`. The adapter receives a repository slug, PR number, expected head SHA,
merge method, runner, bounded poll budget, and sleeper; it returns a typed result carrying
status, message, UUID when present, and merge SHA when present.

**D2 - Direct merges use `merge_action=direct_merge`.** That preserves Worktrail's existing
`merge_method_by_base` behavior. Merge-queue adoption is a separate policy decision because
`merge_method` is only defined for direct merges and because an enqueued PR is not a confirmed
landing for Worktrail's cumulative smoke gate.

**D3 - The expected head SHA is mandatory.** The orchestrator reads the live PR head after its
final gates and passes it as `sha`. If GitHub reports that the head moved or the async result
fails for stale-head reasons, Worktrail does not resubmit against the new head. It returns to
the normal verification path so CI/review gates cover the new commit.

**D4 - HTTP 409 is idempotent only when the existing request matches.** If GitHub says a merge
request is already pending and the returned request has the same expected head SHA,
`direct_merge` action, and requested method, the adapter adopts its UUID and polls it. If those
options differ or cannot be established, the result is a recoverable conflict; Worktrail never
waits on or claims ownership of a semantically different request.

**D5 - Polling is bounded and never re-submits implicitly.** HTTP 202 and an adoptable 409
enter a bounded GET loop. `pending` sleeps with the caller's existing bounded/adaptive timing.
`merged`, `enqueued`, and `failed` are terminal. Budget exhaustion is recoverable and leaves the
PR intact for a later invocation.

**D6 - `enqueued` is not `merged`.** Even though the API calls it a terminal request result,
Worktrail records it in the existing queued/armed class: no cumulative post-merge smoke, no
`merged` journal entry, and no remote branch deletion before GitHub actually lands the PR.

**D7 - Never bypass rules.** Every submission sends `bypass_rules=false`; the adapter has no
public option that can turn it on. A future bypass feature would require its own explicit
specification.

**D8 - Preserve native auto-merge fallback.** If the direct async request reaches terminal
`failed` because branch protection or a human approval still blocks the merge, the current
`gh pr merge --auto` fallback remains available. Existing externally armed auto-merge is still
detected before Worktrail submits any direct request, so Worktrail does not race it.

**D9 - Preserve method fallback above the adapter.** `Verifier` keeps its current preference
order and method-rejection classification. Each candidate method is one async direct request;
a method rejection may try the next allowed method against the same verified head. A non-method
failure does not fan out into speculative retries.

**D10 - Refuse async submission while the PR is still stacked.** Before the PUT, the
orchestrator verifies that the PR's live `baseRefName` equals its canonical `self.base`.
A dependent PR that still targets its parent branch is retargeted/re-verified instead of
calling `merge-async`, preventing GitHub from co-merging an open downstack PR and bypassing
Worktrail's parent post-merge smoke boundary.

**D11 - Existing cleanup replaces `--delete-branch`.** The async endpoint has no
`--delete-branch` flag. For a confirmed `merged` result, `cleanup_group()` already deletes the
remote group branch best-effort, so that becomes authoritative. For `enqueued`, the existing
skip-remote-delete behavior remains mandatory until a later confirmed merge.

## Risks / Trade-offs

- Async polling adds REST calls versus one synchronous CLI invocation, but they are bounded
  and expose a real request identity instead of hiding server-side work behind one command.
- Keeping native `--auto` as a fallback means the repository has two intentional merge
  mechanisms for now: async direct merge for a ready PR, native auto-merge for a PR that must
  wait on future protection/human state. Conflating those semantics would be riskier.
- Refusing a still-stacked PR can add one retarget/reverification cycle, but it protects the
  cumulative regression gate from the async endpoint's downstack co-merge behavior.
- The API result is retained for a limited period by GitHub. This change does not persist UUIDs
  across arbitrarily long sessions; a later invocation re-reads PR state and re-verifies before
  deciding whether a new request is appropriate.
