## Context

See proposal.md for the gap. What the change has to respect, inspected in this checkout:

- `classifier_coverage.audit_coverage()` already returns everything the sweep needs --
  `corpus` (scanned/compared/skipped), `replay` (the pinned state and the withheld-hint note),
  `agreement`, `no_signal`, `by_expected_route`, and `clusters`, each cluster carrying
  `expected`, `predicted`, `count`, `no_signal_count`, `mis_weighted_count`,
  `high_confidence_count`, `from_actual_route_count`, `actionable`, and `sample_briefs`. Its
  replay inputs are pinned (`REPLAY_STATE`, `handoff_route=None`, `pr_states=None`) precisely so
  two runs over one corpus agree; the module performs no writes and, with `pr_states=None`, no
  `gh` lookup.
- The corpus fixture is `tests/fixtures/classifier_corpus.json` (103 outcome-labelled items,
  `label_source: "actual"`). The ratchet test materializes it as a real `queue/` of brief files and
  hands that to `audit_coverage()` unmodified; the same transport works for the sweep.
- Sweep precedent is `src/worktrail/router/spec_sync_sweep*.py`: a composed engine plus small
  single-purpose modules (`_discovery`, `_check`, `_brief`, `_dedup`), a non-blocking `flock`, a
  `--json` run record, and direct brief writes into `queue/`. Its dedup keys on
  `(repo, drift-source)` with a frontmatter marker, not on fuzzy content similarity.
- Brief frontmatter must be canonical: `shared.brief_frontmatter.serialize_frontmatter()` is the
  one documented writer, and `workqueue/check_corpus_style.py` reports any brief whose block does
  not round-trip through it. `spec_sync_sweep_brief.py` hand-rolls its `---` block, which is the
  shape this change must not copy.
- `queue_triage._worktrail_repo_root()` (`Path(__file__).resolve().parents[3]`) is the
  established way an installed worktrail tool resolves its own checkout -- AGENTS.md guarantees
  the editable install always points at the canonical checkout, never a task worktree.
- Tail kinds matter: an `[e2e]` task never joins the fan-out, and `compile.py`'s shape gate
  excludes it from the critical path.

## Goals / Non-Goals

**Goals:**

- Surface a new false-positive family as a counted cluster before it misroutes a dispatch, from
  the signal that already exists, with no new measurement logic.
- Make the evidence the guard-authoring rule requires fall out of the brief automatically:
  counts, the no-signal/mis-weighted split, the confidence, and a sample text.
- Make a scheduled run safe: overlapping runs do nothing, dry runs write nothing, a run that has
  nothing to say is silent, and no run can flood the queue.
- Keep every new file small and independently testable, and leave `classify.py` and
  `classifier_coverage.py` byte-unchanged.

**Non-Goals:**

- Re-surfacing a cluster already filed, or alerting on a cluster that merely grew. A family that
  has been surfaced once is the tracker's, not the sweep's; growth alerting is a follow-up if a
  need is ever shown.
- Tuning the audit's thresholds, its clustering, or its pinned replay defaults.
- The devops cron wrapper and its crontab line. This change ships the engine and documents the
  invocation; the scheduler lives in a different repo, the same split `spec-sync-sweep.sh` uses.
- Filing briefs for any repo but the worktrail checkout the classifier lives in.

## Decisions

### Compose the audit; never re-derive a cluster

The engine calls `classifier_coverage.audit_coverage()` once per corpus and reads its `clusters`
verbatim. Clustering, the `no-signal`/`mis-weighted` split, the `actionable` flag, and the
`MIN_ACTIONABLE_CONFIDENT` threshold stay the audit's; the sweep only *labels* each cluster with
its `origin` and decides whether to file.

*Alternative considered:* re-run `classify()` directly and re-cluster. Rejected -- it would be a
second implementation of a rule that already has one, and the two would drift the first time the
audit's thresholds moved.

### The live queue is the primary corpus; `--corpus` folds in the fixture

The live queue is always replayed. `--corpus <path>` optionally adds the committed
`classifier_corpus.json` fixture, materialized into a scratch queue of brief files exactly as the
ratchet test does, and its clusters are tagged `corpus` rather than `live` so a fixture regression
can never masquerade as a live one. A missing or unreadable `--corpus` is reported and skipped,
not fatal -- the installed CLI must not depend on a repo-layout path, so the devops wrapper is
what supplies it.

*Alternative considered:* drop the corpus and rely on the CI ratchet for fixture regressions.
Rejected: the fixture is the one corpus a `classify.py` change cannot move, so it is the strongest
signal for a *new* regression, and the brief that motivated this change names it explicitly.

### The baseline records what was surfaced, not what was seen

`--baseline` (default `worktrail_home()/classifier-false-positive-sweep/baseline.json`) maps a
cluster key to the count and time it was surfaced. A cluster is reportable when its key is absent
from that map and no unresolved brief for it exists; the map is the sweep's durable dedup, so a
brief that has since been closed does not resurrect a family already reported.

*Alternative considered:* record every observed cluster, so the delta is "new since last run".
Rejected -- that turns a cluster the sweep declined to file (over the cap, or while a brief was
open) into a permanently unreportable one.

### A missing baseline is a recording run, not a filing run

With no snapshot, the sweep writes the baseline and files nothing, reporting the clusters it
seeded in the run record. A first run, or a run after the state file is lost, therefore cannot
emit a burst of briefs for clusters the operator has not asked to see.

### Filing is capped, largest first, and the overflow keeps its place

`--max-briefs` (default 5) bounds a single run. Reportable clusters are ordered by `count`
descending, then by route pair, so the biggest families are filed first; a cluster left over (over
the cap, or skipped because a brief is open) is deliberately *not* written to the baseline, so it
stays eligible on the next run until it is either filed or deduped.

### Dedup is an exact identity, not a similarity match

The brief carries `drift-source: classifier-false-positive-sweep` and
`classifier-cluster: <origin>:<expected>-><predicted>`. The lookup scans `queue/` (any status --
everything there is unresolved) and `picked/` (unresolved unless `status` is `done` or
`superseded`) for that exact pair. A brief that merely discusses the same route pair, or is filed
by a different sweep, is not a match -- the same narrow-identity choice `spec_sync_sweep_dedup`
made for `(repo, drift-source)`.

### The brief is written through the canonical serializer

Frontmatter goes through `shared.brief_frontmatter.serialize_frontmatter()` and the file is
validated with `validate_brief(required=("id", "status", "focus"))` before the run reports it, so
the corpus-style scan reports no finding for a sweep-filed brief and a malformed write fails
loudly instead of landing in the queue.

### The sweep does not set `recommended-route`

A sweep brief is a measurement, not a suggestion: it carries the cluster, the counts, and a
sample text, and lets the front door classify it. Setting a hint would also make the brief's own
route the input to the next replay. This matches `spec_sync_sweep_brief`, which sets none.

### One module per concern, one group per task

The engine composes three small modules (`_delta`, `_brief`, `_dedup`), mirroring
`spec_sync_sweep`'s decomposition. Because `taskformats/openspec/source.py` makes every task in a
group depend on its predecessor, the three independent modules are authored in three separate
`## N.` groups so they can compile as parallel fan-out work rather than a chain.

## Risks / Trade-offs

- [A lost or corrupt baseline stops all filing] → This is the designed failure direction: no
  baseline means a recording run, so the worst case is one missed cycle, never a flood. The run
  record lists the seeded clusters so the loss is visible.
- [The sweep's own briefs enter the live corpus and change later counts] → Sweep briefs carry no
  `recommended-route` and no consuming run record, so `audit_coverage()` skips each as "no
  recorded route"; they cannot seed a cluster or amplify a count.
- [Raw cluster counts grow as the queue grows, so the brief's "delta" is corpus-relative] →
  Accepted and stated in the brief: the delta is rendered as counts against the scanned corpus
  size, which is the same footing the audit's own report uses. Re-alerting on growth is a
  non-goal, so the moving denominator never triggers a second brief on its own.
- [A cluster the cap never reaches stays unfiled] → The cap is per run, not cumulative: an
  unfiled cluster is not recorded in the baseline, so it is re-evaluated and eventually filed as
  larger clusters are cleared, without any state to repair.
- [`--corpus` resolves a path in the repo, which an installed tool should not assume] → The flag
  is optional and a bad path is a reported skip; only the devops wrapper names the fixture, and
  the engine still runs live-only without it.

## Migration Plan

None. The sweep adds one operator-state file under `worktrail_home()` and one console script; it
touches no existing data, config, or queue brief. Rollback is a revert, plus deleting the baseline
file if the operator wants the next run to re-record rather than continue from it. Deploying the
schedule (a devops cron entry invoking the new console script, e.g. `worktrail-classifier-false-positive-sweep
--corpus <checkout>/tests/fixtures/classifier_corpus.json --json`) is a separate step in the devops
repository, after this lands.
