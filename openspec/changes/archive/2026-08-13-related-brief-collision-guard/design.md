## Context

`/go`'s Phase 5.5 already runs two independent pre-dispatch guards:
`check_spec_collision.py` (routes C/D: does a shipped spec already cover this
request?) and `check_brief_staleness.py` (routes E/F: did this brief's own
described work already land?). Both are personal-work-queue-scoped,
fail-open, and advisory-only -- they warn via `AskUserQuestion`, never
auto-close or auto-skip. Neither branch covers routes A, B, G, H, I, J, and
neither one answers a different, adjacent question this change exists to
answer: is a brief **related to** the one just claimed already claimed by
someone else, right now?

The work queue (`$WORK_QUEUE_DIR`, default `~/work-queue`) is a personal,
per-machine directory backed by a private git repo. Sync is push-only from
`work_queue.py` on claim/done/release/link, plus a `*/5` cron
(`~/bin/work-queue-sync.sh`) -- so a machine's local `picked/` directory can
lag another machine's claim by up to five minutes, and this check can only
ever be as fresh as that local clone. This is an accepted, pre-existing
property of the queue (see `feedback_local_env_scripts_in_devops` / the
queue's own docs), not something this change can or should fix.

## Goals / Non-Goals

**Goals:**
- Warn the operator, before Phase 6 opens the run record, when a just-claimed
  brief names a `related:` sibling that is currently `status: picked`
  (claimed and not yet done) by anyone -- including another machine.
- Cover the routes the existing two branches do not (A, B, G, H, I, J),
  without touching or duplicating their logic.
- Stay fail-open and advisory-only, matching the two existing branches'
  contract exactly: `checked: false` on any ambiguity, never an exception,
  never a block, never an auto-mutation of the brief.

**Non-Goals:**
- Real-time cross-machine claim visibility. The check reads whatever is in
  the local `picked/` clone; a claim that hasn't synced yet is invisible to
  it, by design (see Context).
- Verifying that a related brief's *worktree* or *run-record* is still
  literally running (a stale `status: picked` with an abandoned worktree is
  a separate, already-covered concern -- `/go`'s own dashboard surfaces
  stalled-brief resume candidates via `resume` action after ~48h). This
  check answers "is it claimed", not "is it still being actively worked".
- Extending or modifying `check_spec_collision.py` or `check_brief_staleness.py`
  -- this is a third, independent branch, not a refactor of the other two.

## Decisions

**Decision: match on `related:` id list only, not fuzzy overlap.**
The brief being dispatched already carries an explicit `related:` frontmatter
list (used today by `/go`'s "Related Briefs" surfacing and by
`cluster_detect.py`'s duplicate-brief detection). Reusing that existing,
already-curated signal is far cheaper and more precise than re-running
focus-text overlap detection at dispatch time. Alternative considered:
reuse `cluster_detect.py`'s signal-match machinery directly -- rejected,
because that module answers "are these two queued briefs near-duplicates"
(pre-claim triage), a different question from "is this specific named
sibling currently claimed" (post-claim, pre-dispatch), and pulling it in
would couple two independently-evolving capabilities.

**Decision: "active" means `status: picked` in the local queue clone, not a
verified live worktree/run-record.** Cross-machine worktree and run-record
state is not inspectable at all (run records live under `~/.go/runs/`,
never synced anywhere). Approximating "active" with the queue frontmatter's
own `status` field is the same fail-open trade the two existing branches
already make (`check_brief_staleness.py` accepts a possibly-stale local git
clone as its ground truth). When the related brief happens to be claimed by
*this* machine, the check additionally looks for a matching local run record
under `~/.go/runs/<repo>/` (repo resolved from the related brief's `repo:`
field) purely as enrichment -- its absence is not treated as "not active".

**Decision: gate on route, computed by the caller, not by this module.**
Mirroring `check_brief_staleness.py`, this module takes no route argument
and knows nothing about routing; `/go`'s Phase 5.5 decides whether to invoke
it (any route not already handled by the C/D or E/F branches) and passes in
only the claimed brief's path plus the personal queue's `picked/`+`queue/`
directories. Keeping route logic in the skill, not the module, matches the
existing two branches and keeps the module unit-testable without a `/go`
fixture.

**Decision: new module, not an extension of `check_brief_staleness.py`.**
The two checks answer genuinely different questions (git history vs. queue
claim state) with different data sources and different fail-open conditions.
A shared module would need a mode flag threading through the CLI, the result
schema, and every test -- more coupling than the ~140 lines this check
actually needs. Matches this repo's existing pattern of one focused module
per guard (`check_spec_collision.py`, `check_brief_staleness.py`,
`check_repo_freshness.py`).

## Risks / Trade-offs

- [Local queue clone can lag another machine's claim by up to 5 minutes] →
  Accepted; this is the same staleness window every other queue-reading
  operation in this workspace already lives with, and the check is
  fail-open by construction, never fail-closed, so a missed collision costs
  nothing beyond the status quo (no check at all).
- [A related brief's `status: picked` might be an abandoned/stale claim, not
  genuinely active work] → The operator prompt is advisory and shows the
  `claimed-at` timestamp, letting the operator judge staleness themselves
  (the dashboard's own `resume` action already treats a >=48h-old claim as
  likely-abandoned, a threshold this check does not need to duplicate).
- [Extra `AskUserQuestion` interruptions could get noisy on brief clusters
  with many `related:` entries] → Batch all active-claim findings into one
  prompt per dispatch (not one prompt per related brief), same as the
  existing branches' single-prompt-per-dispatch shape.

## Migration Plan

Purely additive: a new module, a new console script, and one new Phase 5.5
branch in `SKILL.md` gated on brief-has-`related:`-entries AND route not
already covered. No existing behavior changes. Rollback is deleting the new
branch's SKILL.md section and console script; nothing else depends on it.
