## Why

Route-classification false positives are still found reactively -- after they have already
misrouted real work. The recorded family so far is #557, #1120, #1131, #1136, and the 2026-10-03
consumer-noun incident fixed in PR #1418. The tooling that finds them exists, and already works:

- `worktrail-classifier-coverage` (`src/worktrail/router/classifier_coverage.py`) replays
  `classify.classify()` over the live work queue (`queue/` + `picked/`) and clusters the
  disagreements by `(expected, predicted)`, splitting `no-signal` (signal-table coverage gaps)
  from `mis-weighted`. The PR #1418 session used exactly this to replay 2675 texts (the 103-item
  outcome-labelled corpus plus 2572 live briefs) as its old-vs-new evidence.
- `tests/router/test_classifier_coverage_ratchet.py` pins the same audit against the committed
  corpus in the `Classifier Coverage Ratchet` CI job.

Neither runs on a schedule. `crontab -l | grep -i classif` is empty, and the nine fleet sweeps in
`~/projects/devops/scripts` cover selfcheck, policy drift, branch prune, missing-ruleset drift,
and delivery audit -- none replays the classifier. The only reader of the signal is a human who
remembers to run the CLI, so a family surfaces only after it has bitten a dispatch, and the
guard-authoring rule every fix is held to -- "Do not widen `_MENTION_ONLY_J_LABELS` /
`_CONSUMER_MENTION_J_LABELS` without its own confirmed false-positive"
(`.claude/skills/router/skill.md`) -- has to be satisfied by hand from a corpus the fix's author
assembles ad hoc, once per incident.

This change puts the sweep's logic in worktrail, where the signal and its reader already live, so
a scheduler only has to invoke it. That is the same split `worktrail-spec-sync-sweep` already uses
(engine in worktrail, `spec-sync-sweep.sh` in devops owning only the cron entry), and the same
reason `worktrail-selfcheck-fleet-sweep.py` keeps every signal in worktrail and only schedules it.

## What Changes

- **A recurring sweep turns the existing audit into briefs.** New console script
  `worktrail-classifier-false-positive-sweep` composes `classifier_coverage.audit_coverage()`
  unmodified -- it never reimplements classification or clustering. It replays the live queue and,
  when given `--corpus`, the committed outcome-labelled corpus fixture, tags every cluster with
  its origin, and compares the result against a persisted baseline of the clusters it has already
  surfaced.
- **Each brief carries the counted evidence the guard rule asks for.** For a cluster absent from
  the baseline the sweep files exactly one brief into `queue/`, naming the cluster, the counted
  old-vs-new delta, the no-signal/mis-weighted split, the high-confidence count, the normative
  `actionable` flag, the pinned replay settings, and at least one sample focus text -- a counted
  false-positive rate over the live corpus, rather than prose the fix's author has to reconstruct
  after the fact.
- **Filing is bounded and deduplicated.** At most `--max-briefs` per run (largest clusters first;
  everything not filed stays eligible for the next run), never while an unresolved brief for that
  cluster is already in `queue/`/`picked/`, and never on a run with no baseline snapshot (a first
  or post-loss run records the baseline and files nothing, so it cannot produce a burst).
- **One run is single-flight, offline, and quiet when there is nothing to say.** A non-blocking
  `flock` makes an overlapping run a no-op; `--dry-run` writes nothing; the run performs no
  network, `gh`, or LLM work; and a completed run exits zero (including "filed nothing") with a
  `--json` record, reserving a non-zero exit for a real queue-write failure.
- **Non-goals:** re-surfacing a cluster that has already been filed (a known family is the
  tracker's job, not the sweep's); changing `classify.py` or its signal tables; changing
  `classifier_coverage.py`'s audit, clustering, or thresholds; and adding the devops cron wrapper
  or crontab entry itself (a separate repo, deployed after this lands).

## Capabilities

### New Capabilities

- `scheduled-classifier-false-positive-sweep`: the recurring, single-flight, deduplicated,
  evidence-carrying sweep that turns the existing classifier-coverage replay into one actionable
  brief per newly surfaced disagreement cluster.

### Modified Capabilities

None.

## Impact

- `src/worktrail/router/classifier_false_positive_sweep.py` -- the composer, its baseline delta,
  and the CLI.
- `src/worktrail/router/classifier_false_positive_sweep_delta.py` -- the baseline snapshot and
  the reportable-cluster rule.
- `src/worktrail/router/classifier_false_positive_sweep_brief.py` -- one brief per cluster, with
  its counted evidence.
- `src/worktrail/router/classifier_false_positive_sweep_dedup.py` -- the unresolved-brief lookup
  keyed by cluster identity.
- `pyproject.toml` -- the `[project.scripts]` entry.
- `tests/router/test_classifier_false_positive_sweep{,_delta,_brief,_dedup}.py`.
- No new runtime dependency, policy key, or queue schema; the sweep's own state is one JSON
  baseline under `worktrail_home()`. No change to `classify.py`, to `classifier_coverage.py`, or
  to any skill.
