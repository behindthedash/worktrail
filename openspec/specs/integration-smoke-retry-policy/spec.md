# integration-smoke-retry-policy Specification

## Purpose
TBD - created by archiving change smoke-gate-flaky-retry-policy. Update Purpose after archive.
## Requirements
### Requirement: integrate_smoke_retries is a policy key

The repository policy SHALL accept an `integrate_smoke_retries` key whose
value is an integer greater than or equal to zero, defaulting to `0`. A value
that is not an integer, is a boolean, or is negative SHALL be dropped to the
default and reported in the loaded policy's `_meta` warnings.

#### Scenario: Key absent

- **WHEN** a repository's policy file does not set `integrate_smoke_retries`
- **THEN** the loaded policy carries `integrate_smoke_retries: 0` and no
  warning about it

#### Scenario: Valid value

- **WHEN** a repository's policy file sets `integrate_smoke_retries: 1`
- **THEN** the loaded policy carries the integer `1`

#### Scenario: Invalid value

- **WHEN** a repository's policy file sets `integrate_smoke_retries` to
  `true`, `"1"`, or `-1`
- **THEN** the loaded policy carries `0` and a `_meta` warning names the key
  and the rejected value

### Requirement: The integrated smoke gate retries non-zero exits when configured

`_run_integration_smoke` SHALL accept a retry count. When the smoke command
exits non-zero and attempts remain, the gate SHALL re-run the same command in
the same integration worktree, and SHALL report success if any attempt exits
zero. With the default count of zero the gate SHALL run the command exactly
once. A timeout or spawn error SHALL fail the gate on the attempt it occurs in
and SHALL NOT be retried. When every attempt fails, the gate SHALL report
failure carrying the last attempt's exit code and output tail, and the call
site SHALL quarantine the group with the existing integration-error category.

#### Scenario: Default count runs once

- **WHEN** the gate runs with the default retry count and the command exits
  non-zero
- **THEN** the command is invoked exactly once and the gate reports failure

#### Scenario: Pass on retry

- **WHEN** the gate runs with one retry and the command exits non-zero on the
  first attempt and zero on the second
- **THEN** the gate reports success and the group proceeds to push and open
  its pull request

#### Scenario: All attempts fail

- **WHEN** the gate runs with one retry and the command exits non-zero on both
  attempts
- **THEN** the command is invoked exactly twice, the gate reports failure with
  the second attempt's exit code and tail, and the group is quarantined with
  the integration-error category with no push or pull request

#### Scenario: Timeout is not retried

- **WHEN** the gate runs with one retry and the first attempt times out
- **THEN** the gate reports the timeout failure without a second invocation

### Requirement: A pass after retry is recorded as flake evidence

When the gate passes on any attempt after the first, the orchestrator SHALL
print a line identifying the group as flaky with the failing attempt's exit
code and output tail, and SHALL record that first-attempt detail in the run
journal under a top-level `smoke_flakes` map keyed by group name, using an
atomic write that leaves the group's own journal record unchanged. A gate that
passes on the first attempt SHALL NOT create a `smoke_flakes` entry.

#### Scenario: Flake recorded

- **WHEN** a group's smoke gate passes on its second attempt
- **THEN** stdout carries a line naming the group as flaky with the first
  attempt's detail, and the run journal's `smoke_flakes` map carries that
  group's name mapped to the same detail

#### Scenario: Clean pass leaves no trace

- **WHEN** a group's smoke gate passes on its first attempt
- **THEN** the run journal carries no `smoke_flakes` entry for that group

#### Scenario: Evidence survives a cold resume

- **WHEN** a run whose journal carries a `smoke_flakes` entry is resumed
- **THEN** the entry is still present after the resumed run rewrites the
  group's own journal record

### Requirement: The retry count reaches the orchestrator from policy

The live orchestrator SHALL resolve `integrate_smoke_retries` from the target
repository's policy and pass it to group integration alongside the smoke
command. When the policy does not set the key, the orchestrator SHALL pass
zero.

#### Scenario: Policy value forwarded

- **WHEN** the target repository's policy sets `integrate_smoke_retries: 1`
- **THEN** group integration runs its smoke gate with one retry

#### Scenario: Unset policy forwards zero

- **WHEN** the target repository's policy does not set the key
- **THEN** group integration runs its smoke gate with zero retries

