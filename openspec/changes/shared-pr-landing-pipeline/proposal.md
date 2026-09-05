## Why

Worktrail lands pull requests through at least six independent paths — four Python
`gh pr create` call sites plus two agent-followed prose procedures — and each one
re-implements a different subset of the mandatory landing steps (compile marker,
`go:risk-*`/`go:no-automerge` labels, pre-PR gate, CI watch loop, review-thread gate,
run-record completion). Every omission has shipped as a live incident: PR #902
(2026-09-02, opened by `queue_triage`'s `propose-change` apply) failed `CI: Scope check`
on its first commit because no `.compile-ok` marker was committed, and nothing watched
the PR because the worktrail-go intake-triage gate says report-and-STOP — a human had to
notice, hand-commit the marker (`454574d2`), and re-run CI. The label family alone has
recurred six times (#74/#80/#82/#128/#137/#281) before being code-enforced inside
`run_record finish`. Prose-only enforcement of the remaining steps keeps producing the
same class of failure; the fix is one code-level pipeline that every PR-producing path
calls, so a step cannot be skipped by omission.

## What Changes

- **New shared PR-landing pipeline** — a single function, `land_pr()`, in
  `src/worktrail/router/land_pr.py`, plus a `worktrail-land-pr` console script for
  agent-driven callers. It performs, in order: (1) for every OpenSpec change directory
  whose `tasks.md` changed against the base branch, run the compile and commit the
  resulting `.compile-ok` marker, refusing to proceed when the marker is missing or
  stale after that attempt; (2) run the preflight gate to compute and record the exact
  `go:risk-<level>` / `go:no-automerge` label set; (3) push; (4) `gh pr create` — or,
  when the head branch already has an open PR, ensure that PR carries the labels — with
  the standard PR body; (5) run the CI watch loop (`gh pr checks --watch`, then classify:
  all-pass / transient rerun / code defect / iteration ceiling), including the
  merge-state guard and the review-thread gate; (6) stamp `pull_request` and
  `merge_result` on the run record and call `finish` with a real completion state
  (or, in checkpoint mode, append a `decisions` entry and return).
- **Callers migrated onto it** (each becomes a thin call, and its own
  `gh pr create` / label / push code is deleted):
  - `workqueue/queue_triage.py` `_worktree_pr_close()` (shared by
    `_apply_fold_into_change` and `_apply_propose_change`) — today: labels only, no
    compile marker, no CI watch, no run record.
  - `router/close_stale_openspec.py` — today it stops before the PR boundary and leaves
    commit/push/PR/watch to prose; it now lands the PR itself after `flip_and_archive`.
  - `drain/drain.py` `_open_sync_pending_pr`, `_open_stale_bookkeeping_pr`,
    `_open_openspec_archive_pr` — today: labels only, no compile marker, no CI watch.
  - `orchestrator/integrate.py` group-PR creation — calls the pipeline's PR-open step
    only (labels + create/update); its CI watch, review-thread resolution and merge stay
    in `verify.py` (non-goal below).
- **Prose reduced to "call the pipeline"**: `worktrail-sdd-workflow/SKILL.md` Phase 8,
  `worktrail-go/references/routes.md` Route C closeout (checkpoint mode),
  `worktrail-go/SKILL.md` close-stale row and Phase 3 CI-watch paragraph,
  `worktrail-go/references/subagent-prompts.md` sync-PR step, and
  `worktrail-sdd-workflow/references/pipeline-details.md` marker note. The ordered step
  list lives in `land_pr.py` docstrings; `ci-watch-loop.md` remains the reference for
  the one judgment step code cannot make (case 4, product decision) and for repairing a
  reported code defect.
- **Intake-triage gate no longer stops before CI**: the apply step's PR is landed
  through the pipeline in-process, so the action-log entry carries the landing outcome;
  `worktrail-go/SKILL.md` Phase 2 reports that outcome and, on a reported code defect,
  repairs it per `ci-watch-loop.md` instead of stopping at "PR opened".
- **Enforcement**: `test_pr_creation_callsite_enforcement_coverage.py`'s
  `KNOWN_CALLSITES` shrinks to `router/land_pr.py` (plus the exempt sandbox-only
  `orchestrator/live.py`); a per-caller unit test proves each migrated caller invokes
  the pipeline; an integration-style test proves the pipeline never pushes when the
  compile marker is missing or stale.
- **Corrected premise**: `router/consolidate_cluster.py` was named as a caller but
  contains no PR-opening code — it writes work-queue briefs only (verified: zero `gh` or
  `git` invocations). Nothing to migrate there; recorded in design.md's inventory.

## Capabilities

### New Capabilities
- `pr-landing-pipeline`: the single code-level sequence every Worktrail path uses to open
  or update a pull request — compile-marker gate, label computation, push, PR
  create/update, CI watch with merge-state and review-thread gates, and run-record
  completion — with fail-closed refusal before push and a bounded, classified watch
  outcome.

### Modified Capabilities
- `intake-triage`: "Fold and propose are applied as a pull request, fail-closed" now
  requires the pull request to be landed through the shared pipeline (compile marker
  committed before push, labels from the preflight gate, CI watched to a classified
  outcome) and the interactive pickup path to report that landing outcome rather than
  stopping at PR creation.

## Impact

- **Code**: new `src/worktrail/router/land_pr.py`; edits to
  `src/worktrail/workqueue/queue_triage.py`, `src/worktrail/router/close_stale_openspec.py`,
  `src/worktrail/drain/drain.py`, `src/worktrail/orchestrator/integrate.py`;
  `pyproject.toml` gains `worktrail-land-pr`.
- **Tests**: new `tests/router/test_land_pr.py` and
  `tests/router/test_land_pr_integration.py`; updates to
  `tests/router/test_pr_creation_callsite_enforcement_coverage.py`,
  `tests/workqueue/test_queue_triage.py`, `tests/router/test_close_stale_openspec.py`,
  `tests/drain/test_drain.py`, `tests/orchestrator/test_integrate.py`;
  `tests/test_plugin_surface.py` and `tests/router/test_skill_prose_enforcement_coverage.py`
  must stay green as skill prose changes.
- **Skills**: prose reductions listed above across `skills/worktrail-sdd-workflow/` and
  `skills/worktrail-go/`.
- **Behavior**: intake-triage apply (`worktrail-skill-dispatch --apply-brief-triage
  --confirm`, and drain's `--intake-triage` pre-pass) and drain's three remediation
  actions now block for the CI watch of the PR they open; callers pass their existing
  `timeout` as the watch budget. Agent-driven Phase 8 replaces four hand-sequenced
  commands with one.
- **Non-goals**: no change to `orchestrator/integrate.py`'s group-PR orchestration
  beyond calling the shared PR-open step (the request names it as
  `conductor/integrate.py`; the file lives under `orchestrator/`); no CI workflow
  changes; no change to `gh pr merge` policy or auto-merge workflows.

## Folded from 20260904-165036-land-pr-push-refusal-wrong

src/worktrail/router/land_pr.py:1008-1009 returns LandOutcome(outcome="refused", refused_step="push") with no detail argument at all, and _push() (land_pr.py:403-424) discards the failed git push's stdout/stderr, only returning the string "push" — confirming the repro's detail=null. The candidate change's declared capability ('fail-closed refusal before push ... with a bounded, classified watch outcome') directly covers fixing this refusal-detail gap, so the fix belongs there rather than as a standalone patch. Live re-repro 2026-09-04 (run go-20260904-165845, worktree fix/spec-drift-shared-pr-landing-pipeline): land-pr refused at push with detail=null while a direct git push -u origin <branch> from the same worktree, and a plain subprocess.run(['git','-C',<worktree>,'push','-u','origin',...]) from Python, both succeeded, so the failure is specific to the runner/env used inside land_pr._push, not the git push itself; capture and report the failed push's stderr in detail. Put the new tests in tests/router/test_land_pr_push_refusal.py (do not extend tests/router/test_land_pr.py: tasks 1.1->1.2 already saturate the compile same-file chain gate on that file).

## Folded from 20260904-180534-land-pr-push-branch-tracking

Same _push() refusal as task 14.1 (openspec/changes/shared-pr-landing-pipeline/tasks.md:230), second repro 2026-09-05 on gracefully-giving-back run go-20260904-171615 (PR #716): the feature branch was created with `git worktree add ... -b <branch> origin/dev` so its upstream is origin/dev, and worktrail-land-pr refused at push (refused_step: push, detail: null) while a manual `git push -u origin <branch>` succeeded and a re-run then landed normally. In src/worktrail/router/land_pr.py, make _push() (land_pr.py:403-424) push with an explicit refspec (`git push -u origin HEAD:<branch>`) so a branch whose upstream tracks the base ref is never pushed to the wrong remote branch, and surface the failed git push's stderr in LandOutcome.detail (land_pr.py:1008-1009) instead of null. Add the tests to tests/router/test_land_pr_push_refusal.py alongside 14.1's (do not extend tests/router/test_land_pr.py).
