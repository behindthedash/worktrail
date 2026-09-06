## Why

The queue-triage `propose-change` and `fold-into-change` apply paths land their pull request
through `router.land_pr` with `run=None`, so the pipeline starts the run record itself
(`land_pr._ensure_run_record`, `src/worktrail/router/land_pr.py:703`). Nothing on that path ever
records a scope-review entry: `grep -n 'scope' src/worktrail/workqueue/queue_triage.py` finds
only non-goals parsing, and `skill_dispatch.py`'s `--apply-brief-triage` branch
(`src/worktrail/router/skill_dispatch.py:973`) just forwards to
`apply_single_brief_verdict`. `run_record.py finish` code-enforces
`pre_pr_gate.scope_review_failures()` for every implementation-completion state
(`src/worktrail/router/run_record.py:766-799`), and that gate returns
`"no scope review recorded in the run record"` for the `scope_review: []` every new record starts
with (`run_record.py:494`, `pre_pr_gate.py:351`). So `finish` raises `SystemExit`,
`_finish_or_checkpoint` (`land_pr.py:941`) returns `False`, and every terminal branch of
`land_pr` falls to `outcome=ceiling / final_status=failed_recoverable` with a
`"PR ... but run record could not be completed"` merge result (`land_pr.py:1298`, `1356`,
`1380`) -- while the record on disk stays at `status=route_selected / final_status=null`.
The `SystemExit` message that explains *why* finish failed goes to stderr and is dropped:
`_run_record_main` (`land_pr.py:685`) captures stdout only, so `LandOutcome.detail` is empty.

Observed live 2026-09-05 on the worktrail queue-triage run that proposed
`agent-capacity-expired-gate-hygiene` (PR #1001): `gh pr view 1001` shows the PR merged at
2026-09-06T01:16:36Z, the brief was closed with `triaged_to` pointing at it, and the run record
was left at `route_selected` with no terminal state. Every triage-proposed change since the
apply path landed has hit the same wall.

A second, independent gap surfaced in the same run: `finish`'s best-effort PR risk-label
correction (`run_record.py:998-1013`) calls `pr_labels.ensure_pr_risk_label` with the record's
`repository`, and `pr_labels._run_gh_cmd` (`src/worktrail/router/pr_labels.py:83`) passes that
as `cwd=`. For a triage landing the repository is the temporary worktree, which
`_worktree_pr_close` removes in its `finally` block -- so any later `finish` against that record
raises `FileNotFoundError` from `subprocess.run`, caught and printed as
`warning: run_record: pr risk-label correction failed`. The `gh` calls involved take a full PR
URL (`gh pr view <url>`, `gh api repos/<owner>/<repo>/...`) and need no working directory at
all; the label is left for `reconcile_pr_labels`' sweep for no reason.

## What Changes

- `land_pr` records the scope review for a run record it started itself. When
  `_ensure_run_record` had to start the record (the caller passed `run=None`), the pipeline
  appends one `scope-review` entry -- item = the request summary, status `complete`, evidence
  naming the pushed commit, branch, and PR URL -- immediately before `_finish_or_checkpoint`
  on every branch that finishes with an implementation-completion state. A caller-supplied run
  record is left untouched: the caller owns its scope review, exactly as today.
- `land_pr` surfaces the run-record failure reason. `_run_record_main` also captures the
  `SystemExit` message (and stderr) so each `"run record could not be completed"` ceiling
  outcome carries the gate's own text in `LandOutcome.detail` instead of an empty string.
- `pr_labels._run_gh_cmd` falls back to no working directory when the repository path no
  longer exists on disk, so the risk-label correction still runs for a full-URL PR after the
  landing worktree is torn down. A bare PR number (which genuinely needs the repository to
  resolve `origin`) keeps its existing warn-and-skip behaviour.
- The triage apply path (`queue_triage._worktree_pr_close`, reached via
  `worktrail-queue-triage apply --confirm` and `worktrail-skill-dispatch --apply-brief-triage`)
  gets a pinned regression: its `LandRequest` passes `run=None` and a non-empty
  `request_summary`, and the returned `landing.final_status` is the pipeline's terminal state,
  not `failed_recoverable`.

## Capabilities

### Modified Capabilities

- `pr-landing-pipeline`: `Run record is completed with a real state` gains the pipeline-owned
  scope-review entry and the failure-reason requirement; a new requirement covers the
  risk-label correction surviving a torn-down landing repository.

## Impact

- **Code**: `src/worktrail/router/land_pr.py` (`_run_record_main`, `_finish_or_checkpoint`,
  `land_pr`, `_ensure_run_record` return shape); `src/worktrail/router/pr_labels.py`
  (`_run_gh_cmd`).
- **Tests**: `tests/router/test_land_pr.py`, `tests/router/test_land_pr_integration.py`,
  `tests/router/test_pr_labels.py`, `tests/workqueue/test_queue_triage.py`.
- **Non-goals**: relaxing `finish`'s scope-completeness gate (it stays a hard block for every
  record); recording scope review on behalf of a caller that supplied its own run record;
  rewriting the record's `repository` to the canonical checkout after the worktree is removed;
  back-filling the already-unfinished record for PR #1001 (operator action, not code);
  any change to `skill_dispatch.py` -- the `--apply-brief-triage` branch is a pure forwarder
  and inherits the fix through `queue_triage` and `land_pr`.
