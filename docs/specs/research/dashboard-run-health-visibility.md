# Investigation — dashboard visibility into in-flight run health (self-merge, post-merge regression)

Route I (investigation) note. Source brief:
`20260807-122949-the-go-orientation-dashboard-dashboard`. Related briefs:
`20260726-221353-port-the-remaining-agent-execution` (unrelated scope — agent-execution
reliability porting, no overlap found), `20260807-023435-add-quarantined-group-visibility-to`
(direct overlap — see below).

Status: **investigation only — no code changes.**

## Problem, as verified

The brief claims the `/go` dashboard does not surface an in-flight orchestrator run's
`QUARANTINED` groups, self-merge violations, or `post_merge_regressed` groups, so a stuck or
partially-failed run looks identical to a healthy one. Verified by reading
`src/worktrail/router/dashboard.py` directly (`grep` for `QUARANTINED`, `self_merged`,
`post_merge_regressed` — zero matches in the file as of `main`@`8653d1a`, the commit this
worktree branched from).

## In-flight overlap — read before touching this

An OpenSpec change `quarantined-group-visibility` is **already in flight** in this repo right
now (worktree `worktrail-worktrees/quarantined-group-visibility-spec`, sub-task worktrees
1.2–2.2/postmerge/verify-feature-1 actively executing at investigation time; brief
`20260807-023435`, claimed 2026-08-07T12:19:38 by `intelnuc:1837384`, ~10 minutes before this
investigation's own brief was captured). Its `proposal.md`/`tasks.md` (read directly) scope is
**strictly `state == "QUARANTINED"` groups**: a new `quarantine_selfcheck.py` module mirroring
`automerge_selfcheck.py`/`policy_drift_selfcheck.py`, wired into `dashboard.py`'s
`scan_repos()`/`render_dashboard()`.

**This investigation's scope is therefore narrowed to the two states that spec does not
cover: `self_merged` and `post_merge_regressed`.** Do not re-plan `QUARANTINED` surfacing —
land via the in-flight PR. Sequence any follow-up work after it merges, both to avoid a
`dashboard.py`/`render_dashboard()` merge conflict and to reuse the exact pattern it
establishes.

## Verified observations

1. **`self_merged` and `post_merge_regressed` are computed in `orchestrator/verify.py`**
   (`Verifier.verify_one`/`verify_and_cleanup`, ~line 1129–1305) as two `Dict[str, str]`
   accumulators (group name → reason), returned in `verify_and_cleanup()`'s result dict
   alongside `merged`/`quarantined`.
2. **They reach `orchestrator/live.py`** (`full_real`, ~line 4154–4209): unpacked from
   `vres`, printed to console (`!! SELF-MERGE VIOLATION: ...` / `!! POST-MERGE REGRESSION:
   ...`), optionally forwarded to a configured `notify_cmd` webhook payload, and returned as
   part of `full_real()`'s own return dict.
3. **They are never written to the run journal** (`<repo>-worktrees/run-<spec_id>.json`).
   Confirmed by reading `_do_journal`/`_write_group_journal` in `orchestrator/integrate.py`
   (~line 374–394): the journal's `groups[name]` schema is exactly
   `{"pr_url", "head_branch", "state"}`, and `state` is populated only from the
   `TERMINAL_GROUP_STATES = {"OPEN", "MERGED", "QUARANTINED"}` enum via `_do_journal(...)`
   call sites — `self_merged`/`post_merge_regressed` are never passed into any `_do_journal`
   call. `Verifier` (the class that computes them) has **no `journal_path` attribute or
   parameter at all** — confirmed by grep, zero matches in `verify.py`.
4. **They are never written to the run record either.** `~/.go/runs/<repo>/<run-id>.yaml` is
   populated exclusively by the calling agent session via the `worktrail-run-record` CLI
   (per `worktrail-sdd-workflow`'s own SKILL.md, Phase 6/8) — `orchestrator/live.py` itself has
   zero references to `run_record`/`RunRecord` (confirmed by grep). So today's only path for
   `self_merged`/`post_merge_regressed` to reach a run record is a human or agent noticing the
   console `!!` lines and manually transcribing them — exactly the unreliable mechanism the
   brief is complaining about, restated: it is not merely un-*surfaced*, it is un-*persisted*.
5. **`dashboard.py` already reads two different sources that could each carry this signal**:
   the run *journal* (via `_journal_verify_pending()` and friends, ~line 732) and the run
   *record* directory (via `load_recent_runs()`, ~line 1866, wired into both single- and
   multi-repo dashboard rows as `recent_runs`, already rendered). Both are real, live
   extension points — not hypothetical.
6. **The brief's own claim** ("only visible via console output at run time or by
   hand-reading the run journal / run record files") is **partially inaccurate**: verified
   observation 3–4 show neither file ever receives these two states today. Console output
   (and `notify_cmd`, if configured) is the *only* current visibility, full stop. Flagging
   this correction explicitly per the no-guessing rule — the brief's framing understated the
   gap rather than overstated it.

## Unknowns / missing evidence

- Whether `notify_cmd` is actually configured for any of this operator's repos (would mean a
  webhook-based safety net already exists independent of the dashboard) — not checked, out of
  scope for a repo-code investigation; would need per-repo `go-policy.yaml` inspection.
- Real-world frequency of `self_merged`/`post_merge_regressed` events — no historical
  incidence data was located in this investigation (unlike the quarantine work, which found 7
  live QUARANTINED groups via direct journal grep, no equivalent grep is possible for these
  two states since they are never written anywhere durable).

## Candidate extension points

### Option A — journal-based (mirrors the in-flight quarantine pattern) — recommended

Add `self_merged`/`post_merge_regressed` as new possible values of the *same* per-group
`state` field the journal already carries (e.g. `"SELF_MERGED"`, `"POST_MERGE_REGRESSED"`),
written via the same `_do_journal`/`_write_group_journal` machinery, from inside
`Verifier.verify_one`/`verify_and_cleanup` — which requires threading a `journal_path`
parameter into `Verifier.__init__`/`verify_and_cleanup()` (currently absent) from
`live.py`'s `full_real()`, which already holds `journal_path` in scope.

- **Pro:** identical shape to `TERMINAL_GROUP_STATES`; a follow-up `*_selfcheck.py` module
  (or an extension to the just-landed `quarantine_selfcheck.py`) can glob the same
  `run-*.json` files with the same `state`-field read the in-flight spec establishes; no new
  cross-subsystem coupling (`orchestrator/` already owns journal writes, `router/` already
  owns journal reads).
- **Con:** `self_merged`/`post_merge_regressed` are not really *terminal* group states in the
  same sense — a self-merged group's underlying PR *did* land; overloading `state` conflates
  "did the group's work reach base" with "was the *process* by which it did so a violation."
  A cleaner shape is a **second top-level journal key** (e.g. `journal["violations"][name] =
  {"kind": "self_merged"|"post_merge_regressed", "reason": ...}`) written alongside, not
  instead of, the existing `state` value — worth deciding explicitly in the follow-up spec's
  proposal, not assumed here.

### Option B — run-record-based

Extend the run record schema (`run_record.py`) with `self_merged`/`post_merge_regressed`
fields, populated by `live.py` calling `worktrail-run-record append`/`finish` directly after
`verify_and_cleanup()` returns, and extend `load_recent_runs()` + the existing `recent_runs`
render block in `dashboard.py` to flag them inline.

- **Pro:** `load_recent_runs()`/`recent_runs` rendering already exists and is already wired
  into both single- and multi-repo dashboard paths — less new rendering code than Option A.
- **Con — decisive:** `orchestrator/` has zero dependency on `router/`'s run-record CLI today
  (verified observation 4) and introducing one inverts the subsystems' current relationship
  (`router/` calls into things `orchestrator/` produces, never the reverse). It would also
  only cover runs actually launched through `worktrail-sdd-workflow`'s Phase 6 run-record
  start — a bare/manual `worktrail-live full-real` invocation outside that flow would still
  produce untracked violations, whereas the journal (Option A) is written unconditionally by
  the orchestrator itself regardless of caller.

**Recommendation: Option A**, specifically the second-top-level-key variant, decided
explicitly (not assumed) in the follow-up spec's `proposal.md`/`design.md` — this repo's own
`design.md` convention (see the in-flight `quarantined-group-visibility` change) is to record
that kind of shape decision up front, not implicitly in code.

## Sequencing

Do not start implementation now. Prerequisite: the in-flight `quarantined-group-visibility`
PR (worktree `worktrail-worktrees/quarantined-group-visibility-spec`) merges to `main` first —
both to avoid a direct `dashboard.py`/`render_dashboard()` conflict and so the follow-up spec
can literally extend `quarantine_selfcheck.py` (or add a sibling module reusing its
`sweep()`/`main()`/console-script conventions, per the `policy_drift_selfcheck.py` precedent)
rather than re-deriving the pattern from scratch.

## Recommended next route

**Route C (feature planning)**, once the quarantine-visibility PR merges — this needs a
`design.md` shape decision (Option A's journal-key question above) before task breakdown, so
it is not a small, clearly-scoped Route F/H fix that should continue in this same run. Do not
open a follow-up handoff for this — the recommended next action is fully specified above; a
future `/go` session should route straight to Route C against this note once the dependency
PR is on `main`.
