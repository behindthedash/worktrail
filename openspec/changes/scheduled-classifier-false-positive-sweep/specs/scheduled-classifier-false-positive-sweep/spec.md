## Purpose

Turns the existing, hand-run classifier coverage audit into a recurring sweep: it replays the
route classifier over the live work queue and the committed outcome-labelled fixture, and files
exactly one deduplicated, evidence-carrying brief per disagreement cluster it has not surfaced
before, so a false-positive family is measured before it misroutes a dispatch instead of
reconstructed after one.

## ADDED Requirements

### Requirement: Sweep replays the live queue and each supplied corpus through the shared audit

The sweep SHALL obtain every disagreement cluster by calling
`classifier_coverage.audit_coverage()` unmodified, and SHALL NOT re-implement classification,
clustering, the no-signal/mis-weighted split, or the actionable threshold. The live work queue
(`queue/` and `picked/`) SHALL always be replayed. When a corpus fixture path is supplied, its
items SHALL be materialized as a queue of brief files and replayed the same way. Every cluster
SHALL carry an `origin` of `live` or `corpus` in its identity, so a fixture cluster can never be
confused with a live one, and a corpus path that is absent or unreadable SHALL be reported and
skipped rather than failing the run.

#### Scenario: The live queue alone is replayed
- **WHEN** the sweep runs with no corpus fixture supplied
- **THEN** it reports the clusters of the live `queue/` and `picked/` briefs, each tagged `live`,
  and completes without a corpus result

#### Scenario: A supplied corpus is folded in as its own origin
- **WHEN** the sweep runs with a corpus fixture whose disagreements include a route pair the live
  queue also disagrees on
- **THEN** the fixture disagreement is reported as a `corpus` cluster distinct from the `live`
  cluster for the same route pair

#### Scenario: An absent corpus path is skipped, not fatal
- **WHEN** the sweep runs with a corpus path that does not exist or cannot be read
- **THEN** the run completes, its record reports the skipped corpus, and no cluster is filed for it

### Requirement: Sweep files one deduplicated brief per newly surfaced cluster

For each cluster whose key is absent from the persisted baseline snapshot, the sweep SHALL file at
most one brief, into the queue root's `queue/`, carrying that cluster's counted evidence: the
counted old-vs-new delta against the baseline, the no-signal and mis-weighted counts, the
high-confidence count, the actionable flag, the pinned replay settings, and at least one sample
focus text drawn from the cluster's briefs. Exactly one brief SHALL be filed per cluster
regardless of how many disagreeing briefs the cluster contains. The sweep SHALL NOT file a brief
while an unresolved brief for that same cluster identity already exists in `queue/` or in
`picked/`, and SHALL file no more than the configured per-run maximum, taking the largest clusters
first. A cluster that is not filed SHALL NOT be recorded in the baseline, so it stays reportable
on a later run; a cluster that is filed, or found to have an unresolved brief already, SHALL be
recorded so it is not surfaced again. The written brief's frontmatter SHALL be produced through
the canonical brief serializer and validated before the run reports it filed.

#### Scenario: A new cluster becomes one brief with its counted evidence
- **WHEN** a cluster absent from the baseline is reported with a count, a split of no-signal and
  mis-weighted disagreements, and sample brief ids
- **THEN** exactly one brief is written into `queue/` naming that cluster, its counted baseline
  delta, the split, the replay settings, and at least one sample focus text

#### Scenario: A cluster with many disagreements still yields one brief
- **WHEN** a reportable cluster contains several disagreeing briefs
- **THEN** one brief is filed for the cluster, not one per disagreeing brief

#### Scenario: An already-surfaced cluster is not filed again
- **WHEN** a cluster is present in the baseline snapshot
- **THEN** no brief is filed for it

#### Scenario: An unresolved brief blocks a second one for the same cluster
- **WHEN** a reportable cluster already has an unresolved brief in `queue/`, or in `picked/` with
  a status other than `done` or `superseded`
- **THEN** no second brief is filed, the run reports the cluster as skipped for an existing brief,
  and the cluster is recorded in the baseline

#### Scenario: The per-run maximum bounds filing without losing the overflow
- **WHEN** more clusters are reportable than the configured per-run maximum
- **THEN** only the maximum number are filed, largest count first, and every cluster not filed
  remains reportable on the next run

### Requirement: A sweep run is single-flight, non-mutating under dry-run, and machine-readable

The sweep SHALL take a non-blocking exclusive lock before doing any discovery, replay, or write;
when another run holds it, the run SHALL return immediately having done nothing and SHALL report
the overlap. A `--dry-run` run SHALL write no brief and SHALL NOT advance the baseline snapshot. A
completed run SHALL exit zero -- including one that filed nothing and one that was skipped as an
overlap -- and SHALL emit a machine-readable record under a JSON option describing the corpora
scanned, the clusters reportable, the briefs filed, the clusters skipped, and the overlap state.
A failure to write into the queue SHALL be reported loudly and SHALL exit non-zero.

#### Scenario: An overlapping run does nothing
- **WHEN** a run starts while another sweep run already holds the lock
- **THEN** it performs no replay and writes no brief, reports the overlap, and exits zero

#### Scenario: A dry run writes nothing
- **WHEN** the sweep is run in dry-run mode against reportable clusters
- **THEN** no brief is written, the baseline is unchanged, and the run report lists the clusters it
  would have filed

#### Scenario: A run with nothing to file is a quiet success
- **WHEN** no cluster is reportable
- **THEN** the run exits zero and its report shows nothing filed

#### Scenario: A queue write failure is loud
- **WHEN** writing a brief into the queue fails
- **THEN** the run surfaces the error and exits non-zero

### Requirement: The sweep is offline and deterministic over the audit's pinned replay inputs

The sweep SHALL perform no network call, no `gh` lookup, and no model invocation, and SHALL pass
the audit its pinned replay inputs -- the pinned classifier state, the documented `resumable_state`
default, and a withheld `handoff_route` hint with no PR states -- so two runs over the same corpora
and settings produce the same clusters. The run SHALL record the replay settings it used, and SHALL
advance the baseline snapshot with the clusters it surfaced, so the next run's delta is measured
against this run's outcome.

#### Scenario: Two runs over the same corpora agree
- **WHEN** the sweep is run twice with the same corpora and settings, the second time against the
  baseline the first run wrote
- **THEN** the second run reports the same clusters, files nothing new, and exits zero

#### Scenario: The replay settings are recorded
- **WHEN** a run files a brief or emits its record
- **THEN** the pinned state, the `resumable_state` value, and the withheld-hint condition are
  carried in that output
