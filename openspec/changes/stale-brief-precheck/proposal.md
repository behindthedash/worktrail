## Why

`/go`'s Phase 5.5 guard already answers one pre-dispatch question — "does an existing
`docs/specs/` spec already cover this request?" — via `check_spec_collision.py`. That guard is
deliberately gated to **routes C and D only**, and its own reference documents why: a bugfix is a
change to existing code, not a new spec that could collide with a shipped one.

That reasoning is correct for *spec* collision, but it leaves a different staleness failure
completely unguarded. A queued brief describing a defect can be delivered by some *other* PR
between the moment it was captured and the moment it is claimed. Nothing in the dispatch path
notices. The brief is claimed, classified, given a run record, and handed a worktree before
anyone discovers there is nothing left to do.

Concrete instance, from the brief that motivated this change:
brief `20260731-204048` (`prevent-destructive-commands.py` squash-merge + `cd`-prefix
verification) was fully delivered by `behindthedash/devops` PR #89, merged 2026-08-02 — one day
after capture. It stayed claimable for five more days until a 2026-08-05 session burned a full
dispatch verifying it. The verification itself was cheap — roughly four tool calls
(`git log -S` on the named symbols, `gh pr view`, one test run). The waste was not the
*checking*; it was that the checking happened after a claim, a classification, and a run record
rather than before them.

The evidence needed is bounded and near-free: the brief names symbols, files, and sometimes PR
numbers, and it carries a `created:` timestamp that bounds the search window. Asking git
"did anything touch these since this date?" costs milliseconds.

## What Changes

- A new best-effort `check_brief_staleness` capability that, given a brief's focus text, its
  `created:` timestamp, and a repo, extracts bounded evidence probes (path-shaped tokens,
  identifier-shaped symbols, explicit `PR #NNN` references) and searches the repo's base-branch
  history for changes matching them since the brief was captured.
- A new `worktrail-check-brief-staleness` console script exposing it, mirroring the CLI shape
  of the existing `check_spec_collision` / `check_repo_freshness` guards.
- Phase 5.5 of the `worktrail-go` skill gains a second, complementary branch: brief-sourced
  dispatches resolved to **route E or F** run the staleness check before Phase 6 opens the run
  record. Routes C/D keep running the spec-collision check exactly as they do today; neither
  branch changes the other.
- When evidence is found, `/go` surfaces the candidate commits/PRs to the operator via
  `AskUserQuestion` and **never auto-closes the brief**. Closing stays an explicit human call.
- Every failure and every ambiguity fails **open**: a repo that is not a git checkout, an
  unparseable brief, a git or `gh` failure, a timeout, or simply no matching evidence all leave
  the dispatch proceeding exactly as it does today.

Deliberately out of scope: the batch queue-triager (brief `20260731-210136`) is a scheduled
monthly ~1M-token sweep over the *whole* queue. This change is an inline, per-dispatch,
near-zero-cost check on the *one brief being started*. They compose — the triager catches
backlog rot, this catches the brief you are about to work on — and neither replaces the other.

## Capabilities

### New Capabilities
- `stale-brief-precheck`: bounded, fail-open evidence extraction and base-branch history search
  that detects when a claimed brief's described work may already have landed, and surfaces the
  candidates to the operator before a code-fix dispatch spawns a worktree.

### Modified Capabilities
<!-- None. No existing spec in openspec/specs/ owns Phase 5.5; the spec-collision guard is
     procedure documented in the worktrail-go skill, and this change adds a sibling branch to
     that procedure without altering the collision guard's own requirements or behavior. -->

## Impact

**New code**
- `src/worktrail/router/check_brief_staleness.py` — extraction + search, best-effort, never raises.
- `tests/router/test_check_brief_staleness.py` — unit coverage for extraction, search, and every
  fail-open degradation path.

**Modified code**
- `pyproject.toml` — register the `worktrail-check-brief-staleness` console script (and the
  version bump this repo's `CI: Version Bump Check` requires for any `src/worktrail/**` change).

**Modified procedure (Claude Code plugin surface)**
- `skills/worktrail-go/SKILL.md` — Phase 5.5's gate description gains the E/F branch.
- `skills/worktrail-go/references/spec-collision-check.md` — documents both branches, or hands
  off to a sibling reference for the staleness branch.

**Enforcement already in place that this must satisfy**
- `tests/test_plugin_surface.py` — every `worktrail-*` command named in a skill doc must be a
  real console-script entry point, and `references/*.md` cross-links must resolve.
- `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
  must stay green (the repo's `pre_pr_cmd`).

**Not affected**
- `check_spec_collision.py` and the C/D branch of Phase 5.5 — untouched, no behavior change.
- Queue lifecycle: this capability never moves, stamps, or closes a brief. `work_queue.py`
  remains the single owner of that lifecycle, invoked only by an explicit operator decision.
