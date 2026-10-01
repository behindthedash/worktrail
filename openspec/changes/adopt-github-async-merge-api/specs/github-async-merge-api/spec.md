## ADDED Requirements

### Requirement: Async merge requests are immutable, head-bound operations
Every Worktrail-owned asynchronous direct merge request SHALL target the exact live PR head
that passed Worktrail's mergeability, CI, and review-thread gates by sending that head as the
request `sha`. The request SHALL use `merge_action=direct_merge`, SHALL use the selected
direct merge method, and SHALL set `bypass_rules=false`. Worktrail SHALL NOT expose an option
that enables rule bypass. When GitHub reports an already-pending request for the same PR,
Worktrail SHALL adopt and poll it only when its expected head, merge action, and merge method
match the operation Worktrail intended; otherwise Worktrail SHALL report a recoverable conflict
without claiming that request as its own.

#### Scenario: New request is accepted
- **WHEN** a verified PR at head `abc123` is submitted for a squash direct merge
- **THEN** the request sends `sha=abc123`, `merge_method=squash`,
  `merge_action=direct_merge`, and `bypass_rules=false`, and HTTP 202 enters result polling

#### Scenario: PR was already merged
- **WHEN** the PUT returns HTTP 200 with terminal status `merged`
- **THEN** Worktrail returns a confirmed merged result without issuing a second merge request

#### Scenario: Matching request is already pending
- **WHEN** the PUT returns HTTP 409 with a UUID whose expected head, action, and method match
  Worktrail's request
- **THEN** Worktrail adopts that UUID and polls it rather than creating another request

#### Scenario: Different request is already pending
- **WHEN** the PUT returns HTTP 409 but the returned expected head, action, or method differs
  from Worktrail's intended operation or cannot be established
- **THEN** Worktrail reports a recoverable conflict and does not poll it as its own request

#### Scenario: Rules are never bypassed
- **WHEN** any direct async merge is submitted
- **THEN** `bypass_rules` is false and no caller can override it

### Requirement: Async merge results are polled to a typed terminal outcome
After an accepted or safely adopted asynchronous merge request, Worktrail SHALL poll the
request UUID within a bounded budget. `pending` SHALL remain non-terminal. `merged`,
`enqueued`, and `failed` SHALL be distinct terminal outcomes. Worktrail SHALL preserve the
server's failure detail and merge SHA when present. Poll-budget exhaustion, malformed data,
or an unavailable result endpoint SHALL be recoverable failures and SHALL NOT cause an
implicit new merge request.

#### Scenario: Pending request completes as merged
- **WHEN** one or more GET responses are `pending` and a later response is `merged`
- **THEN** Worktrail returns `merged` with the merge SHA when GitHub supplied it

#### Scenario: Request is enqueued
- **WHEN** the result endpoint returns `enqueued`
- **THEN** Worktrail returns `enqueued` distinctly from `merged`

#### Scenario: Request fails
- **WHEN** the result endpoint returns `failed` with a message
- **THEN** Worktrail returns a failed terminal result carrying that message

#### Scenario: Poll budget is exhausted
- **WHEN** the result remains `pending` through the bounded poll budget
- **THEN** Worktrail reports a recoverable timeout and does not submit another merge request

### Requirement: Orchestrator direct merges use the async endpoint after all gates
The orchestrator SHALL submit a Worktrail-owned direct merge only after its existing
mergeability, required-CI, and review-thread gates have passed. Immediately before submission
it SHALL read the live PR head and base. The base SHALL equal the orchestrator's canonical base
branch; a PR that still targets a parent feature branch SHALL NOT be submitted to `merge-async`.
The live head SHALL be the expected head passed to the async request. Existing externally armed
auto-merge SHALL continue to win: when the PR already carries an auto-merge request, Worktrail
SHALL wait on that external mechanism instead of racing it with an async direct merge.

#### Scenario: Green canonical-base PR is merged asynchronously
- **WHEN** mergeability, CI, and review-thread gates pass, the PR targets the canonical base,
  and no external auto-merge is armed
- **THEN** the orchestrator submits an async direct merge bound to the live head instead of
  invoking synchronous direct `gh pr merge`

#### Scenario: Dependent PR is still stacked
- **WHEN** a dependent PR still targets its parent feature branch after the gates run
- **THEN** the orchestrator does not submit `merge-async` and re-enters its retarget/verify
  handling, so GitHub cannot co-merge open downstack PRs ahead of Worktrail's safety gates

#### Scenario: Head changes after verification
- **WHEN** GitHub rejects or fails the async request because the expected head no longer
  matches the PR head
- **THEN** Worktrail re-verifies the new head before any later merge request

#### Scenario: Configured method is rejected
- **WHEN** an async direct merge fails with the existing recognized merge-method rejection
  signal
- **THEN** the orchestrator may try its next allowed merge method against the same verified
  head, preserving the existing fallback order

#### Scenario: External auto-merge was already armed
- **WHEN** the live PR already has an auto-merge request before Worktrail submits a direct merge
- **THEN** Worktrail does not submit `merge-async` and waits on the external merge as before

### Requirement: Async outcomes preserve cleanup and post-merge safety
Only a terminal async result of `merged` SHALL count as a confirmed orchestrator merge.
`enqueued` SHALL be treated as queued/armed but not merged. Cumulative post-merge smoke SHALL
run only after a confirmed `merged` result. Remote group-branch deletion SHALL occur through
the existing cleanup path after a confirmed merge, and SHALL be skipped while an `enqueued`
PR still depends on that branch. A direct async failure caused by branch protection or an
unmet human gate MAY continue through the existing native auto-merge arming fallback; that
fallback SHALL remain distinct from a confirmed async merge.

#### Scenario: Confirmed async merge runs the cumulative gate
- **WHEN** the async result is `merged`
- **THEN** the group is eligible for cumulative post-merge smoke and normal merged cleanup

#### Scenario: Enqueued result is not post-merge
- **WHEN** the async result is `enqueued`
- **THEN** the group is recorded as queued/armed, cumulative post-merge smoke does not run,
  and the remote group branch is not deleted by Worktrail

#### Scenario: Branch protection requires future action
- **WHEN** the direct async request fails because branch protection or a human approval still
  blocks the merge
- **THEN** the orchestrator may use its existing native `--auto` arming fallback and does not
  report the PR as already merged

### Requirement: Synchronous direct merge regressions are rejected
Worktrail SHALL have an automated source-level guard that rejects new package call sites which
perform a synchronous direct `gh pr merge`. The guard MAY allow the explicitly retained
`gh pr merge --auto` arming fallback because that path represents a different persistent
wait-for-protection behavior. Direct merge behavior SHALL flow through the async merge adapter.

#### Scenario: New synchronous direct merge call is added
- **WHEN** a source change under the Worktrail package adds a direct `gh pr merge` invocation
  without the retained `--auto` semantics
- **THEN** the regression test fails and names the offending source file

#### Scenario: Native auto-merge fallback remains
- **WHEN** the source contains the documented `gh pr merge --auto` fallback used after a
  protection-blocked direct attempt
- **THEN** the regression guard permits that call site
