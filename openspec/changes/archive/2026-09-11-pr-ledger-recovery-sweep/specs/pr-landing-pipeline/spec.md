## MODIFIED Requirements

### Requirement: Every PR-opening path lands through the shared pipeline

Every code path in Worktrail that opens or updates a pull request — the queue-triage
fold/propose apply, the close-stale OpenSpec action, drain's sync-pending,
stale-bookkeeping, and archive remediations, the orchestrator's group-PR creation, and the
agent-executed spec/implementation closeout — SHALL do so by invoking the shared landing
pipeline. No such path SHALL construct its own `gh pr create` invocation, compute its own
label set, or decide on its own whether to watch CI. The only permitted exception is a
sandbox-only development tool that never produces a policy-governed pull request. Once the
pipeline creates or discovers its PR, it SHALL register that PR in the durable recovery ledger
before returning its landing outcome.

#### Scenario: Queue-triage fold or propose opens a PR

- **WHEN** `apply --confirm` executes a `fold-into-change` or `propose-change` verdict
- **THEN** the pull request is opened through the shared pipeline, is registered for recovery,
  and the apply result carries the pipeline's landing outcome alongside the PR URL

#### Scenario: Close-stale action opens a PR

- **WHEN** the close-stale OpenSpec action has flipped the remaining checkboxes and archived
  the change in its worktree
- **THEN** the same action lands and registers the resulting pull request through the shared
  pipeline rather than leaving commit, push, PR, and CI watch to the calling agent

#### Scenario: Drain remediation opens a PR

- **WHEN** a drain remediation action (sync-pending, stale-bookkeeping, or OpenSpec archive)
  has committed its change on a short-lived branch
- **THEN** the pull request is opened and registered through the shared pipeline with the
  action's own timeout as the CI watch budget

#### Scenario: Existing PR is resumed by the pipeline

- **WHEN** the pipeline finds an existing open pull request for its head branch
- **THEN** it registers that PR idempotently before resuming its CI watch

#### Scenario: New hand-rolled PR creation is rejected

- **WHEN** a new `gh pr create` call site appears anywhere under the package other than the
  pipeline module or the registered sandbox-only exception
- **THEN** the call-site enforcement test fails and names the unregistered file
