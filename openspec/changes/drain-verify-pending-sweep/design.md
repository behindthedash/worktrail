## Context

`drain.py` already has one "resumable sweep" pattern: `resume_quarantined_budget_exhausted()`
finds every `(repo, spec)` pair with a `QUARANTINED`/`budget_exhausted` group (via
`quarantine_selfcheck.check_repo()`) and re-runs `worktrail-live full-real` for it — no
`--fresh`, since `full-real`'s own `resume=True` default just continues the interrupted fan-out
from its run journal. This sweep runs once before the queue loop starts and once again after it
finishes (only if the loop actually ran an iteration).

A second, more common stalled-work shape exists: `dashboard.py`'s `detect_stage()` already
labels a spec `stage: "verify-pending"` when `_journal_verify_pending(spec_dir)` is true —
implementation groups are done (`integrate_complete: true` in the run journal) but at least one
group's PR hasn't actually landed on the base branch yet. The exact same `worktrail-live
full-real` re-run resolves this too (it drives verify → merge → cleanup from wherever the
journal left off). Today nothing calls `full-real` for these; a human has to notice the
dashboard's "Needs verify / merge → resume full-real" line.

## Goals / Non-Goals

**Goals:**
- Detect every spec across `--repos-root` currently in the `verify-pending` stage.
- Resume each one with the identical `full-real` re-run shape the quarantine sweep already
  uses (`resolve_spec_rel` + `build_full_real_resume_command`).
- Reuse `dashboard.py`'s existing `detect_stage()`/`scan()` for detection — no parallel
  re-implementation of `_journal_verify_pending`'s journal-reading logic.
- Wire the new sweep into `drain()`'s existing pre-loop and post-loop sweep points, alongside
  the quarantine sweep, so one drain invocation covers both categories.

**Non-Goals:**
- No change to `_journal_verify_pending()`, `detect_stage()`, or any dashboard rendering —
  this change is a new *consumer* of that existing detection, not a modification of it.
- No change to `resume_quarantined_budget_exhausted()`'s own behavior or the quarantine
  detection path (`quarantine_selfcheck.py`).
- No new CLI flags. The sweep runs unconditionally whenever `--repos-root` is already set
  (same gating `resume_quarantined_budget_exhausted` uses today), so existing quarantine-sweep
  users get the new coverage automatically.
- No handling of specs whose repo has since been deleted from `--repos-root`, or whose journal
  file has gone missing — `_journal_verify_pending` already returns `False` for those (dead
  journal → not pending), matching the quarantine sweep's own best-effort posture.

## Decisions

**Detection: reuse `dashboard.scan()` instead of re-deriving journal logic.**
`resume_quarantined_budget_exhausted` calls into `quarantine_selfcheck.check_repo()`, a sibling
detector module. The `verify-pending` stage has no equivalent standalone detector — its logic
lives inline inside `detect_stage()`. Two options were considered:
1. Extract `_journal_verify_pending`'s journal-reading into a new sibling module
   (`verify_pending_selfcheck.py`), mirroring `quarantine_selfcheck.py`'s shape exactly.
2. Call `dashboard.scan(repo/"docs"/"specs")` directly and filter rows where
   `stage == "verify-pending"`.

Chose **(2)**. `scan()` already performs the full per-spec stage detection (devkit specs under
`docs/specs/` *and* OpenSpec changes under `openspec/changes/`, since `scan()` derives
`repo_root` from `specs_root` and also enumerates `_openspec_change_dirs(repo_root)`) with
existing per-spec error isolation (`_safe_detect_stage`/`_safe_detect_openspec` degrade one
broken spec to an `error` row instead of raising) and existing concurrency
(`ThreadPoolExecutor`). Re-deriving a second reader of the same run-journal file risks the two
detectors drifting (e.g. one gets `_group_merged_on_base`'s stale-bookkeeping fix, the other
doesn't) — a class of bug this repo has hit before with `_pending_impl_stale` vs
`_pending_tail_stale`. Filtering `scan()`'s output is a few lines and guarantees the sweep sees
exactly what the dashboard shows a human, by construction.

**New helper: `find_verify_pending_specs(repos_root, go_repo=None)`.**
Mirrors `find_resumable_quarantines()`'s signature and shape exactly (same repo-name
discovery via `discover_repo_names`, same `go_repo` single-repo filter, same
`resolve_spec_rel` call to get the `--spec` path, same "skip on missing spec_rel" best-effort
behavior). Returns `List[Dict[str, Any]]` with the same key shape (`repo`, `repo_name`,
`spec_id`, `spec_rel`) so the resume function below can reuse `build_full_real_resume_command`
and `_base_branch_for` unchanged.

**New sweep function: `resume_verify_pending(repos_root, go_repo, agent, timeout, spawner, log)`.**
Mirrors `resume_quarantined_budget_exhausted()`'s signature and body shape exactly (same
per-finding loop, same log line shape prefixed `resume-verify-pending:` instead of
`resume-quarantine:`, same best-effort "one spec failing doesn't stop the others"). Kept as a
separate function rather than generalizing both sweeps into one parameterized function: the two
detection sources (`quarantine_selfcheck.check_repo()` vs `dashboard.scan()`) have different
return shapes and error-handling needs, and the existing quarantine sweep is stable,
already-tested code — a generalization would touch it for no behavior change. Two small,
near-identical functions reading clearly is preferred over one function branching on a detector
callback for a two-case matrix that isn't expected to grow past two.

**Wiring: same two call sites as the quarantine sweep, same order (quarantine sweep first).**
Both sweeps run pre-loop and post-loop (post-loop only when `state.iteration > 0`, matching the
existing re-sweep guard's rationale: an empty queue pass means nothing could have changed since
the pre-loop sweep). Order is quarantine-sweep-then-verify-pending-sweep at both points —
arbitrary but stable, since the two sweeps target disjoint spec sets (a `QUARANTINED` spec is
never simultaneously `verify-pending`; `detect_stage()`'s stage field is single-valued per
spec) and never interact. `resumed_quarantines` in the summary dict gains no new key; a new
`resumed_verify_pending` key is added alongside it in the returned summary dict, populated the
same way.

## Risks / Trade-offs

- [Risk] `dashboard.scan()` walks every spec in a repo (not just quarantined/pending ones) to
  produce its rows, which is more work per sweep than `quarantine_selfcheck.check_repo()`'s
  narrower quarantine-only scan. → Mitigation: `scan()` is already the exact call `/go`'s own
  dashboard makes on every invocation across every repo; the additional cost inside `drain.py`
  (which already iterates `discover_repo_names` once for the quarantine sweep) is the same
  per-repo cost the dashboard already pays interactively, not a new order of magnitude.
- [Risk] A spec could flip from `verify-pending` to some other stage (e.g. `done`) between the
  detection call and the `full-real` resume call, if something else resolves it concurrently
  (another drain instance, a human). → Mitigation: `full-real` is idempotent/resumable by
  design (this is the same property the quarantine sweep already depends on) — re-running it
  against a spec whose journal now shows all groups `MERGED` is a fast no-op, not a hazard.
- [Risk] Two sweeps (quarantine + verify-pending) now both call `full-real` per drain
  invocation, roughly doubling worst-case sweep-phase wall time when both categories have
  hits. → Mitigation: both are already best-effort, non-blocking of the main queue loop (a
  slow sweep just delays the loop start, same as today's single quarantine sweep already can).
  No change needed; flagged as an accepted trade-off, not one requiring new bounding logic in
  this change.

## Migration Plan

Purely additive — no flag, no opt-out. Existing `drain.py --repos-root ...` invocations
automatically gain the new sweep on upgrade. No data migration; the run journals this reads are
already-existing files no other change touches.

## Open Questions

None outstanding — behavior, detection source, and wiring points are all pinned above.
