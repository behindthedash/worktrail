## Why

The orchestrator's only launch-time exclusivity is `RunLock` — a `flock` on a sidecar beside
the run journal, keyed by `(repo, spec)` (`src/worktrail/orchestrator/live.py:371-405`), and the
journal it guards is itself keyed by spec folder name (`journal_path_for`, `live.py:646`). Two
`full-real` runs for *different* specs on the same repo therefore start with nothing detecting
each other — a grep for any repo-level guard (`another orchestrator|repo-level|repo_wide`) over
`src/worktrail/` finds nothing — and the never-run-two-orchestrators-per-repo rule exists only
as operator memory (`feedback_never_run_concurrent_orchestrators_same_repo`: "Never dispatch a
second `worktrail-live full-real` … even for a different spec"), not as code.

That gap was observed degrading a run on 2026-10-02/03. Run `go-20261002-220652`
(`model-tier-routing-zero-usage-spawn-detection`) overlapped a `spawn-readiness-preflight`
full-real on the same repo; task 1.1's own implement report recorded the full-repo suite "not
run to completion (exceeded 600s foreground cap under concurrent worker load)", and the run then
spent a 27m49s tail task re-running it. The same class of overlap is why merges from one run
stale the other's task worktrees (2026-08-31/09-01: ~10 resume attempts of hand-rebasing).

The repo-wide scan the doctrine assumes does not exist either. `worktrail-run-record
active-conflicts` requires `--specification` (`run_record.py:2520-2523`), and `_active_conflicts()`
filters every record on that one string; the cross-spec `--expected-files` claim check
(`_file_overlap_conflicts`) only sees what a caller registered at claim time; `drain.py:1883`
reuses the same spec-keyed scan for its sweep guard. A launch that wanted to know "is any other
run live on this repo?" cannot ask — and nothing asks for it.

## What Changes

- **Repo-wide scan mode.** `worktrail-run-record active-conflicts --specification` becomes
  optional; omitting it scans every non-terminal run record for the repo, keeping the existing
  live/stale partition (same `_is_stale()` rule, same malformed-record skip into `warnings`) and
  naming each entry's own `specification` so a caller can tell runs apart. The
  specification-filtered scan behaves exactly as today.
- **Launch-time detection.** A `full-real` launch, after acquiring its per-spec run lock and
  before resolving its fan-out width, scans for other live runs on the same repo — any
  specification, excluding its own record — and, when it finds one, prints one warning naming
  the repo and the count, plus one line per run naming its id, specification, and record path,
  before any worker spawns.
- **Width back-pressure.** A launch that found another live same-repo run caps its effective
  fan-out width at `max(1, floor(base / 2))` and says so in the width report it already prints,
  so two same-repo runs together consume roughly one run's worker slots instead of two.
- **Per-spec exclusivity unchanged.** A second run for the same spec still aborts on `RunLock`
  before any scan; same-spec records are never counted as "another" run. No hard repo-level lock
  is introduced, so legitimately disjoint-spec concurrency stays possible — just no longer
  silent or unthrottled.
- Document the repo-wide scan mode and the launch-time behavior in the skill reference that
  carries the anti-collision procedure, since the operator rule it replaces was doctrine only.

## Capabilities

### New Capabilities

- `same-repo-run-concurrency-contract`: launch-time detection of other live runs on the same
  repo (any specification), a warning naming them, and a halving cap on this launch's fan-out
  width.

### Modified Capabilities

- `active-conflicts-staleness-reconciliation`: the scan's specification filter becomes optional
  (omitted = repo-wide), and every partition entry names the record's own `specification`.

## Impact

- **Code**: `src/worktrail/router/run_record.py` (optional `--specification`; `specification` on
  partition entries); `src/worktrail/orchestrator/live.py` (detection helper, warning, width cap
  in `_resolve_max_workers`/`_pipeline_scheduler`).
- **Tests**: `tests/router/test_run_record.py` (repo-wide scan) and a new
  `tests/orchestrator/test_same_repo_run_concurrency.py` (detection helper, width cap, one
  end-to-end launch through the lifecycle harness's real-`_full_real_inner` fixture).
- **Docs**: `skills/worktrail-go/references/subagent-prompts.md` (`#active-conflicts-scan`).
- **Compatibility**: no policy, run-record schema, or CLI break; a launch with no same-repo
  conflict behaves exactly as today. The per-spec hard stop, drain's spec-keyed sweep guard, and
  the `--expected-files` claim check are untouched. The additional `specification` key on
  partition entries is additive.
