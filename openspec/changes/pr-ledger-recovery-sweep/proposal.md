## Why

The shared PR-landing pipeline is now the required in-process route for opening
Worktrail PRs, and it watches CI while its caller remains alive. That is not a
durable recovery guarantee. A watcher can time out or crash, and an interactive
agent can still type `gh pr create` through the preflight hook rather than
calling `land_pr()`. In both cases the PR can remain open with failing or
blocked CI and no owner that will return to it. The observed instance was
devops PR #353 on 2026-09-02: triage opened the PR and the agent stopped at
“PR opened”.

There is currently no PR ledger in `src/`, `hooks/`, or `tests/`; consequently
there is no durable inventory to sweep and no session-end guard for PRs the
session opened. The prerequisite shared landing work is archived as
`2026-09-05-shared-pr-landing-pipeline`.

## What Changes

- Add a durable, atomic PR ledger under `worktrail_home()` that records an open
  PR URL, repository identity, opener, optional run/brief identity, opening
  session, and opening time.
- Have `land_pr()` register every PR it creates or finds, and have the
  preflight hook path register a successful agent-typed `gh pr create` as well.
- Add `worktrail-pr-ledger sweep`: it queries each recorded PR, removes merged
  entries, leaves a green PR with auto-merge armed alone, and creates one
  deduplicated `pr fix` queue brief when an unwatched PR is red, blocked, or
  open past the configured pacing threshold. The command is suitable for the
  existing five-minute reconciliation job.
- Extend the Claude Stop hook to block session termination while an open ledger
  entry belongs to that session, with a concise recovery instruction. It stays
  fail-open and remains excluded for headless workers.

## Capabilities

### New Capabilities

- `pr-ledger-recovery`: durable registration, periodic recovery triage, and
  session-end ownership protection for Worktrail-managed pull requests.

### Modified Capabilities

- `pr-landing-pipeline`: every PR discovered or opened by the shared pipeline
  is durably registered for recovery before the caller receives its outcome.

## Impact

- New router module and console entry point for the ledger/sweep; landing and
  preflight integration in `src/worktrail/router/`; Stop-hook changes in
  `hooks/suggest_next_step.py`.
- Tests cover atomic/deduplicated ledger behavior, a red-CI sweep that files a
  fix brief using a fake opener, both registration paths, and Stop-hook
  blocking.
- The existing `*/5` machine-level reconciliation job is not versioned in this
  repository. Deploying this change requires its owner to add
  `worktrail-pr-ledger sweep` beside `worktrail-reconcile-pr-labels`; the
  package supplies the deterministic, idempotent command but does not create
  or edit an external crontab.
