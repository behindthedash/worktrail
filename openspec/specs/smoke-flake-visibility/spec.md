# smoke-flake-visibility Specification

## Purpose
Makes recorded integration smoke-test flakes visible to the operator, so a suite that the
retry policy silently rescued run after run is surfaced as a recurring signal instead of
staying buried in run journals nobody reads.
## Requirements
### Requirement: Aggregate recorded smoke flakes per repository

The system SHALL provide a smoke-flake detector that scans every run journal belonging to a
repository, reads each journal's recorded smoke-flake entries, and aggregates them by suite
name into one entry per suite carrying: the suite name, the number of distinct runs in which
that suite flaked, the identifiers of those runs, and the first-attempt failure detail from
the most recent of them.

Aggregation SHALL count a suite once per run journal, never once per journal entry, so the
reported count is "how many runs this suite flaked in".

Entries SHALL be ordered by run count descending, so the most persistently unreliable suite
is reported first.

#### Scenario: One suite flakes across several runs

- **WHEN** three run journals each record a smoke flake for the suite `base`, and one of them
  also records a flake for the suite `e2e`
- **THEN** the detector reports two entries, `base` with a count of 3 listing all three run
  identifiers and `e2e` with a count of 1
- **AND** `base` is ordered before `e2e`

#### Scenario: Repository has no recorded flakes

- **WHEN** a repository's run journals record no smoke flakes at all, or the repository has
  no run journals
- **THEN** the detector reports no entries and does not fail

### Requirement: Bound aggregation to a recency window

The detector SHALL consider only run journals last modified within a recency window, so a
suite that was fixed long ago ages out of the aggregate rather than accumulating across every
journal ever written. The window SHALL default to 30 days and SHALL be overridable by the
caller.

Journals outside the window SHALL be ignored entirely — they contribute neither a count nor a
run identifier to any entry.

#### Scenario: Stale journal is excluded from the count

- **WHEN** one journal recording a flake for suite `base` was last modified 200 days ago and
  another recording a flake for the same suite was last modified yesterday, under the default
  window
- **THEN** the detector reports `base` with a count of 1, naming only the recent run

#### Scenario: Caller widens the window

- **WHEN** the same two journals are scanned with a window wide enough to include both
- **THEN** the detector reports `base` with a count of 2, naming both runs

### Requirement: Classify recurring flakes distinctly from single flakes

Each reported entry SHALL carry a recurrence classification: a suite that flaked in two or
more runs within the window is `recurring`, and a suite that flaked in exactly one run is
`single`. Both are reported; the classification distinguishes the documented act-on-it signal
from a one-off without hiding either.

#### Scenario: Mixed recurrence in one repository

- **WHEN** suite `base` flaked in 2 runs and suite `lint` flaked in 1 run, both inside the
  window
- **THEN** `base` is classified `recurring` and `lint` is classified `single`, and both appear
  in the detector's output

### Requirement: Detector never breaks its caller

The detector SHALL be passive: it SHALL NOT write to, repair, or delete any journal, SHALL
make no network calls, and SHALL NOT gate, block, or fail any run.

A journal that cannot be read or does not parse SHALL be skipped silently and SHALL NOT
prevent the remaining journals from being aggregated. A journal whose smoke-flake data is
present but not of the expected shape SHALL likewise be skipped rather than raising.

A run journal that is currently held by a live run SHALL still be included: a flake recorded
by a run that is still in progress is still a flake.

#### Scenario: Malformed journal among valid ones

- **WHEN** one journal in the window contains invalid JSON and two others record flakes for
  suite `base`
- **THEN** the detector reports `base` with a count of 2 and raises no error

#### Scenario: Live run's journal is included

- **WHEN** a journal recording a flake for suite `base` belongs to a run that currently holds
  its run lock
- **THEN** that journal's flake is counted in the aggregate

### Requirement: Surface smoke flakes on the orientation dashboard

The orientation dashboard SHALL render a smoke-flake section whenever the detector reports at
least one entry for the repositories in scope. The section SHALL name the affected suites with
their run counts, SHALL indicate how many further suites were omitted when more exist than are
displayed, and SHALL end with a next-action pointer directing the operator to fix the flaky
suite — matching the shape of the dashboard's other detector sections.

When the detector reports no entries, the dashboard SHALL render no smoke-flake section at
all.

#### Scenario: Recurring flake is rendered

- **WHEN** the dashboard is rendered for a repository whose detector reports suite `base` as
  recurring with a count of 3
- **THEN** the rendered dashboard contains a smoke-flake line naming `base` and its count of 3
  and a next-action pointer

#### Scenario: No flakes renders no section

- **WHEN** the dashboard is rendered and the detector reports no entries
- **THEN** the rendered dashboard contains no smoke-flake section

### Requirement: Expose smoke flakes on the dashboard JSON payload

The dashboard's JSON output SHALL carry the detector's aggregate under a dedicated key, so a
programmatic consumer reads the same entries the rendered text is derived from. The key SHALL
be present whenever the dashboard emits JSON, carrying an empty aggregate when there are no
entries rather than being absent. The published dashboard JSON field contract SHALL document
the key.

#### Scenario: JSON consumer reads the aggregate

- **WHEN** the dashboard emits JSON for a repository whose detector reports one recurring
  suite
- **THEN** the payload's smoke-flake key carries that entry with its suite name, run count,
  run identifiers, recurrence classification, and most recent detail

#### Scenario: Key present when clean

- **WHEN** the dashboard emits JSON and the detector reports no entries
- **THEN** the smoke-flake key is present and carries an empty aggregate

### Requirement: Detector is runnable standalone

The detector SHALL be invocable as a standalone command accepting a repository path and an
optional JSON output flag, exiting 0 when no entries are reported and 1 when at least one is,
matching the invocation and exit-code convention of the workspace's sibling self-check
commands.

#### Scenario: Clean repository via CLI

- **WHEN** the command is run against a repository whose journals record no smoke flakes
- **THEN** it exits 0 and reports that there are no findings

#### Scenario: Flaking repository via CLI with JSON

- **WHEN** the command is run with the JSON flag against a repository whose journals record a
  recurring flake for suite `base`
- **THEN** it exits 1 and its JSON output carries the `base` entry

