## Purpose

Ensures a pull request opened by Worktrail remains recoverable after its
original CI watcher or interactive session ends.

## ADDED Requirements

### Requirement: Open pull requests have durable, deduplicated ledger entries

The system SHALL persist a ledger entry keyed by canonical pull-request URL for
every PR successfully created or discovered by `land_pr()` and every successful
agent-typed `gh pr create` admitted by the preflight hook. An entry SHALL carry
repository identity, opener, optional run/brief identity, opening session when
available, opening time, and recovery metadata. Re-registering the same URL
SHALL be idempotent and SHALL preserve existing provenance rather than creating
a duplicate. Ledger writes SHALL be atomic, and an unreadable ledger SHALL not
be silently replaced.

#### Scenario: Pipeline creates a pull request

- **WHEN** `land_pr()` creates a PR or finds an existing open PR for its branch
- **THEN** the canonical URL has one durable ledger entry before the landing
  result is returned

#### Scenario: Interactive preflight-protected creation succeeds

- **WHEN** an agent-typed `gh pr create` succeeds after the preflight hook
- **THEN** the resulting PR is registered with opener `preflight` and the
  current session identity when available

#### Scenario: Registration is retried

- **WHEN** either opening path registers a URL already in the ledger
- **THEN** no second entry is created and original opening provenance remains

### Requirement: Periodic sweep creates one recovery brief for an unwatched non-terminal PR

The `worktrail-pr-ledger sweep` command SHALL inspect recorded PRs without
creating, pushing, or merging a PR. It SHALL remove merged entries, retain an
open green PR whose auto-merge is armed, and create exactly one deduplicated
`pr fix` work-queue brief for an open PR that is red, merge-blocked, or past the
pacing threshold when no live watcher heartbeat is fresh. The brief SHALL name
the PR URL, repository, and observed state. A GitHub query failure SHALL retain
the entry and report an error without filing a speculative brief.

#### Scenario: Red CI after watcher loss

- **WHEN** a ledger PR has failing checks and no fresh watcher heartbeat
- **THEN** sweep records the observation and files one `pr fix` brief for drain

#### Scenario: Later sweep sees the same red PR

- **WHEN** the same entry already names its recovery brief
- **THEN** sweep does not file another brief

#### Scenario: PR is merged

- **WHEN** sweep observes that a ledger PR is merged
- **THEN** it removes that entry and files no recovery brief

### Requirement: Interactive session end is guarded by its open PRs

The Claude Stop hook SHALL query the ledger for non-terminal PRs opened by the
current session before emitting its ordinary next-step suggestion. When such an
entry exists it SHALL block with a recovery instruction naming the PR. The hook
SHALL remain a no-op for headless workers and SHALL fail open on unavailable
command, timeout, malformed response, or ledger-read error.

#### Scenario: Session owns an open PR

- **WHEN** the Stop hook receives a session ID with a non-terminal ledger entry
- **THEN** it blocks session termination and identifies that PR for recovery

#### Scenario: Another session owns the PR

- **WHEN** the ledger entry belongs to a different session
- **THEN** the current session is not blocked by that entry
