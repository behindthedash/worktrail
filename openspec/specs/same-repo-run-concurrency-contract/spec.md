# same-repo-run-concurrency-contract Specification

## Purpose
Makes a same-repo concurrent orchestrator launch visible and self-limiting: per-spec exclusivity
stays a hard stop, while a run that launches alongside another live run on the same repository
reports the collision and narrows its own fan-out instead of silently doubling the machine's
worker load.
## Requirements
### Requirement: Same-repo live runs are detected before fan-out

Every real orchestrator launch SHALL, after it has acquired its per-spec run lock and before it
resolves its fan-out width, scan that repository's run records for other live runs regardless of
specification. The scan SHALL classify records with the live/stale rule the active-conflicts scan
already uses: a record whose worktree is gone and whose files already resolve on its own base
branch is not live, and a malformed record is skipped and reported as a warning rather than
aborting the launch. Records whose `specification` equals this run's own spec id SHALL NOT be
reported as another run.

#### Scenario: A different spec is live on the same repo

- **WHEN** a launch for spec A begins on a repo while a non-terminal record for spec B (its
  worktree still on disk) exists for the same repo
- **THEN** the launch reports spec B's run as a same-repo live run and applies the width cap

#### Scenario: The launching run's own record is not a conflict

- **WHEN** the repo-wide scan finds the launching run's own record (its `specification` equals
  this run's spec id)
- **THEN** nothing is reported for it and the effective width is the width resolved today

#### Scenario: A stale record is not a conflict

- **WHEN** the only other non-terminal record for the repo is stale (its worktree is gone and
  its files already resolve on its base branch)
- **THEN** nothing is reported for it and the effective width is unchanged

#### Scenario: Same-spec exclusivity still runs first

- **WHEN** a second launch begins for a spec whose run lock is held
- **THEN** it aborts on the run lock exactly as today, and the repo-wide scan is not required of
  it

### Requirement: A detected same-repo run is reported loudly

When the launch scan finds one or more other live runs, the launch SHALL, before any worker is
spawned, print one warning line naming the repository and the number of runs found, plus one
line per run naming at least its run id, its specification, and its run-record path.

#### Scenario: Warning names every live run

- **WHEN** the launch scan finds a live run for spec B and a live run whose specification is
  `fix:some-slug`
- **THEN** the launch prints a warning naming the repo and the count, and one line per run
  carrying that run's id, its specification, and its record path

#### Scenario: A record with no specification is still named

- **WHEN** the launch scan finds a live non-terminal record whose `specification` is unset
- **THEN** the launch reports it as a same-repo live run, naming its run id and record path

### Requirement: Same-repo concurrency halves the effective fan-out width

A launch that found at least one other live run on the repo SHALL cap its effective fan-out width
at `max(1, floor(base / 2))`, where `base` is the width it would otherwise use (an explicit
`--max-workers` value, else the policy's `max_workers`, else the compiled plan's width capped by
`max_parallel_workers`). The capped width SHALL be the width the fan-out actually runs with, and
the launch's existing width report SHALL state the capped value together with the same-repo
concurrency reason and the number of other live runs found. When no other live run is found, the
effective width SHALL be exactly the width resolved without this rule.

#### Scenario: Width is halved with a reason

- **WHEN** the width would otherwise be 4 and one other live run exists for the repo
- **THEN** the fan-out runs with 2 workers and the width report names the same-repo concurrency
  and the number of other live runs

#### Scenario: Width never falls below one

- **WHEN** the width would otherwise be 2 or 1 and another live run exists for the repo
- **THEN** the effective width is 1 and the run still fans out

#### Scenario: More than one other live run halves the width once

- **WHEN** more than one other live run exists for the repo
- **THEN** the width is capped once, at `max(1, floor(base / 2))`

#### Scenario: No other live run detected

- **WHEN** the scan finds no other live run for the repo
- **THEN** the effective width is exactly the width resolved today and no same-repo concurrency
  warning is printed

