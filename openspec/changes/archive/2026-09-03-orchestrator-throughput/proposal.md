## Why

Run `full-1788369246` (change `autonomous-intake-brief-convergence`, 2026-09-02, journal
`~/projects/worktrail-worktrees/run-autonomous-intake-brief-convergence.json`) shows the
spec-to-implementation pipeline spending most of its wall-clock on work the plan itself created:
18 tasks compiled to critical path 9 / width 5; across the run the journal records 48 min of
implement time against 116 min of review time and 31 min of fix loops (9 implement, 12 review,
4 fix entries); tasks 1.1 and 3.1 each implemented in under a minute and then reviewed for 10
and 18 minutes. Two of four feature groups quarantined with `task_failure` for a reason that was
authored into `tasks.md`: task 1.2 (`create_handoff.py`) and task 4.1 (`queue_triage.py`)
intentionally changed behavior that existing tests assert, their `files:` scope excluded those
test files (the tests were assigned to sibling tasks 1.3 / 4.3), the reviewer correctly returned
`review_status: FAILED` on the red suite, and the fixer either refused as out of scope ("no
in-scope change possible, no commit made") or burned three strikes. The bounded scope escalation
already in `live.py` never fired because it requires a *fix* report with `status: failed` plus
concrete paths in `missing_context`; the evidence fixers put the paths in `notes` with
`missing_context: []`, and 1.2's fix reports were `status: success`. Separately, every code PR
from the run (#907, #908) arrived red at CI's `ruff format --check` because no worker runs the
formatter and neither `pre_pr_cmd` nor `integrate_smoke_cmd` includes it.

## What Changes

- **Task authoring produces coarse, co-scoped tasks.** The bundled `openspec-propose` skill's
  tasks-artifact step gains explicit rules: one implementation task per module per phase sized
  for roughly 20-60 minutes, consecutive same-file steps folded into one task with sub-bullets,
  an implementation task's `files:` must include every test file that asserts behavior it changes
  plus the new test file it adds (implementation and its tests are never split), and mechanical
  or docs-only tasks carry `review: skip` for the existing review-exempt fast path. Composes with
  the active change `wider-dag-task-generation-guidance` (per-phase hot-file ownership); does not
  restate it.
- **`review:` becomes declarable in `tasks.md`.** OpenSpec's checklist currently carries only a
  `files:` continuation line; `review` reaches a task solely from the compile model's
  `light|standard|deep` vocabulary, so `review: skip` is unreachable for an OpenSpec task today
  (`[docs]` is the only authored exemption). An indented `review:` continuation line, parsed the
  same way as `files:`, closes that gap.
- **`worktrail-compile` rejects bad plan shape instead of warning.** Three new problems, each
  exit 1 and naming the task ids and the remedy: critical path longer than
  `max(width, compile_max_critical_path_over_width)` (policy key, default 2); more than
  `compile_max_same_file_chain` (policy key, default 2) consecutive dependent tasks whose `files:`
  is the same single file; an implementation task naming a `src/` path but no `tests/` path when
  that module already has a tests counterpart. The orchestrator's pre-fan-out compile and the
  worktrail-go pipeline's scope-check step inherit the rejection with no prose change, because
  both already treat a non-zero compile exit as a stop.
- **Review cost proportional to change size.** New policy key `review_skip_max_diff_lines`
  (default 0 = disabled; this repo sets 40): when the implement worker reports tests passed and
  its commit diff, excluding test files, is under the threshold, the review spawn is skipped and
  the journal entry records `review_status: skipped-small-diff`. The review role is routed to a
  faster tier through the existing `routing.roles.review` knob (see design for where that file
  lives).
- **Out-of-scope review findings expand scope instead of quarantining.** Reviewer and fixer
  prompts require every file the worker needed but could not touch to be listed in
  `missing_context` as a repo-relative path. A reviewer `FAILED` report carrying such paths is
  itself an escalation trigger; escalation adds the paths to the task's `files:` for the remaining
  strikes (honoring the existing no-collision-with-in-flight-task rule), re-dispatches the fix once
  more before counting a strike, never escalates or quarantines a task while an unconsumed
  escalation is pending, and records `scope_escalated_files` on the journal entry.
- **Workers format and lint before every commit.** New policy key `pre_commit_cmd` (default
  None) is a hard rule in implement/fix/ci-fix worker prompts and is re-run deterministically
  after each task commit, amending when it changed files. `worktrail-repo-init` seeds it from the
  repo's CI lint steps when it can detect ruff, oxlint, or prettier. This repo's policy sets
  `pre_commit_cmd: "ruff check . --fix && ruff format ."` and adds
  `ruff check . && ruff format --check .` to `integrate_smoke_cmd`.
- **This change's own `tasks.md` obeys the new authoring rules**: one task per module with
  implementation and tests co-scoped, `review: skip` on the config/prose tasks, one `[e2e]` tail.

Non-goals: fold-candidate ranking or triage logic (other active changes), the 3-strike ceiling,
how groups are formed beyond what compile rejects, and a per-group `review_mode` (deferred; see
design).

## Capabilities

### New Capabilities
- `task-authoring-co-scoping-guidance`: the `openspec-propose` tasks-artifact rules for task
  granularity, implementation/test co-scoping, and `review: skip` on mechanical tasks.
- `compile-plan-shape-gate`: `worktrail-compile`'s rejection of serial, same-file-chained, and
  test-less plans, with the two policy keys that tune it.
- `review-cost-fast-path`: the policy-driven small-diff review skip and its journal record.
- `fix-scope-escalation`: reviewer- and fixer-triggered bounded scope escalation, the
  pending-escalation guard, and the journal record.
- `worker-pre-commit-command`: the `pre_commit_cmd` policy key, its prompt rule, its deterministic
  post-commit backstop, and its repo-init seeding.

### Modified Capabilities
- `openspec-task-file-declaration`: adds an inline `review:` continuation line alongside the
  existing `files:` declaration so an authored task can opt out of review.

## Impact

- `src/worktrail/conductor/parallelism.py`, `compile.py`: shape problems, policy-tuned thresholds,
  exit-1 rejection; `orchestrator/live.py` inherits via `apply_run_plan`.
- `src/worktrail/orchestrator/live.py`: small-diff review skip, reviewer-triggered scope escalation,
  pending-escalation guard, `pre_commit_cmd` post-commit backstop, journal fields.
- `src/worktrail/orchestrator/dispatch.py`: `missing_context` contract in reviewer/fixer prompts,
  `pre_commit_cmd` hard rule in implement/fix/ci-fix prompts.
- `src/worktrail/router/policy.py`: `compile_max_critical_path_over_width`,
  `compile_max_same_file_chain`, `review_skip_max_diff_lines`, `pre_commit_cmd`;
  `onboarding/repo_init.py`: seeding from detected CI lint steps.
- `src/worktrail/taskformats/openspec/schema.py`, `source.py`: `review:` continuation line.
- `skills/openspec-propose/SKILL.md`: new authoring rules; `tests/test_plugin_surface.py` prose
  assertion.
- `.worktrail/policy.yaml`: `pre_commit_cmd`, `integrate_smoke_cmd`, `review_skip_max_diff_lines`.
- Operator step outside the repo: `~/.worktrail/routing.yaml` `roles.review.tier`.
- Journal schema additions are extra keys; `reconcile_from_journal` ignores unknown keys, so
  existing journals resume unchanged.
