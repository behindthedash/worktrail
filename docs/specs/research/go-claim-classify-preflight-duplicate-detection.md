# Investigation: Go's claim/classify/preflight path has no duplicate-work check

Brief: `20260730-184604-go-s-claim-classify-preflight`
Run record: `go-20260730-190633`

## Verified Observations

- `src/worktrail/router/preflight.py`'s `check()` (lines 103-132) resolves and evaluates
  only `pre_pr_cmd`/`integrate_smoke_cmd` and the pass-marker keyed to tree state
  (`tree_state()`, HEAD sha + working-tree status + diff digest). It contains no logic that
  queries `gh pr list`, inspects sibling worktree directories, or compares against a brief
  id or branch-name slug.
- `src/worktrail/router/dashboard.py`'s `_find_worktrees()` (lines 1386-1396) lists worktree
  directory names under `<repo>-worktrees/` for display only ("so the dashboard can offer a
  stale-worktree cleanup action" — comment at line 1415). It does not correlate those names
  against open PRs or the brief/branch currently being claimed.
- `dashboard.py`'s `staleness_warnings` field (`_staleness_warnings()`, lines 2019-2037) is
  produced entirely by `check_repo_freshness.check()` — i.e. whether the base checkout is
  behind `origin` — unrelated to duplicate PRs or orphaned worktrees.
- `src/worktrail/router/classify.py` line 72 has an `existing-work` signal
  (`r"\bworktree\b|\bmy branch\b|\bopen pr\b|\bexisting (branch|pr)\b"`) that nudges the
  route toward E only when the *user's free-text request* happens to contain those words.
  It is not a proactive scan run against the brief/branch being dispatched.
- No file under `src/worktrail/router/` or `src/worktrail/orchestrator/` calls `gh pr list`
  or `gh pr view` to search for an existing open PR before a claim/classify/dispatch cycle
  (`grep -rn "gh pr" src/worktrail/router src/worktrail/orchestrator`, reviewed every hit).
- The brief's cited evidence is independently verifiable via `gh`: PR #63
  (`wire-plan-audit-into-verify`, MERGED 2026-07-31T01:43:48Z, "fix: log verify.py's
  touched-vs-declared mismatch as a plan-audit signal") and PR #64
  (`investigate/wire-plan-audit-into-verify`, CLOSED 2026-07-31T01:45:23Z, "investigate(verify):
  log plan_audit's compile-accuracy signal automatically") implement the identical change to
  `_forbidden_paths_touched` in `verify.py` — two branches from two separate sessions both
  produced the same fix in the same two-minute window and neither was flagged as
  possibly-duplicate before PR creation.
- `git worktree list` at investigation time shows 17 worktrees under
  `~/projects/worktrail-worktrees/`; `gh pr list --repo behindthedash/worktrail --state open`
  returns zero open PRs. Every current worktree is therefore orphaned relative to open PRs —
  none would be caught by a PR-correlation check on its own, since a worktree can go stale by
  its PR merging *or* by nobody ever opening one. (The specific
  `investigate/wire-plan-audit-into-verify` worktree the brief describes was already torn
  down manually before this investigation started — consistent with the brief's own account
  of finding and closing it by hand.)

## Unknowns / Missing Evidence

- Whether the prior session's claim/classify/preflight run for brief `20260730-182002` (the
  session that produced PR #64) ran with the *same* repo state as the session that produced
  PR #63, i.e. whether a check running at PR #64's claim time would even have found PR #63
  yet (PR #63 and #64 closed/merged within ~2 minutes of each other; no run-record evidence
  from that session was available to this investigation to establish ordering precisely).

## Hypotheses

None needed — the absence of duplicate-detection logic in `preflight.py`, `dashboard.py`,
and `classify.py` is directly confirmed by reading each file's full relevant logic (see
Verified Observations), not inferred from behavior.

## Confirmed Root Cause

Go's claim → classify → preflight path has no step that checks whether a brief's id, its
derived branch-name slug, or the target repo's existing worktrees already correspond to an
open PR before dispatch proceeds. `preflight.py` only gates the pre-PR test command;
`dashboard.py` only lists worktree names for a human to eyeball; `classify.py`'s
`existing-work` signal fires on request text, not on an automated scan. A resumed or
independently-dispatched session for equivalent work can therefore reach `gh pr create`
without ever being warned that a matching PR or worktree already exists — as demonstrated by
PR #63/#64.

## Recommended Next Route

**Route J — Workflow Evolution.** The fix requested (grep open PRs and local worktrees for
the brief id/branch-name slug, surface a warning before dispatch) modifies
`dashboard.py` and/or `preflight.py` in `src/worktrail/router/` — GO's own dispatch
machinery — which routes.md §J explicitly scopes ("Changes to GO, skills, plugins, agent
prompts, orchestration, cassettes — this is production code"). It is a new capability (an
active check + surfaced warning), not the "minimal diagnostics" carve-out Route I permits
(contrast with PR #63/#64's own precedent: a single non-blocking `self.log()` call with a
byte-for-byte-unchanged return value, which Route I's no-code-changes-except-diagnostics rule
allows). Per routes.md §I's own transition rule, only a confirmed root cause with a small fix
*clearly in scope for Route F* may continue inline; this fix does not qualify, so this run
stops here rather than improvising a Route J change without the routing-cassette and
adverse-effect gates that route requires.

Suggested approach for the Route J follow-up: add a check (likely in `preflight.py`'s
`check()`, since that is the single choke point both the `/go` dispatch path and the
PreToolUse hook already call before `gh pr create`) that derives the expected branch-name
slug from the brief id/focus, greps `gh pr list --state open` and the sibling
`<repo>-worktrees/` directory for a match, and returns a warning (not necessarily a hard
"deny," since a legitimate resume should proceed) surfaced through the existing JSON verdict
contract.
