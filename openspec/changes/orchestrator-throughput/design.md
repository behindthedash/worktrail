## Context

See proposal.md for the evidence run. The relevant current state, all verified in this worktree:

- `conductor/parallelism.py` computes `Profile(tasks, critical_path, width, hot_files)` and
  `format_warning()`; its docstring says "never fails a compile". `compile.py::main()` prints the
  warning to stderr and returns 0 on it; `live.py::apply_run_plan()` prints the same lines via
  `_parallelism_lines()` and fans out anyway. `compile_run_plan()` has exactly two callers:
  `compile.main()` and `live.py` (`apply_run_plan`, and `_planned_tasks_without_llm` for
  precheck). A resumed run with a pinned plan never calls it.
- OpenSpec `tasks.md` parsing (`taskformats/openspec/schema.py`) recognizes one continuation
  line, `files:`. The task-level `review` field exists on `TaskPlan` and is merged by
  `runplan.apply_to_tasks()` only when the task has no value of its own, but its only source is
  the compile model's `light|standard|deep` vocabulary. `live.py::_review_exempt()` honors
  `review: skip|false|no|none|off` or `kind: docs`. So for an OpenSpec task the only authorable
  exemption today is the `[docs]` tag.
- `live.py::_scope_escalation_files()` requires `report["status"] == "failed"`, a non-empty
  `missing_context` of existing repo-relative files outside the task's scope, no prior escalation,
  and no collision with an in-flight task's files. `drive()` calls it only for `ROLE_FIX`, after
  `_commit_step()` has already applied `dispatch.transition()` (which increments `retry_count`
  on a `FAILED` review and returns `escalated` at `MAX_REVIEW_RETRIES`). Replay
  (`reconcile_from_journal`) restores a widened scope from `scope_escalated` +
  `scope_added_files` and forces status `fixing`.
- `dispatch.transition()` raises on any review `review_status` other than `PASSED|FAILED`; the
  replay loop swallows that `ValueError` and skips the entry.
- Worker prompts are pure functions of a ctx (`build_worker_prompt`, `build_group_prompt`).
  Task-level ctx is built in `live.py`; ci-fix/resolve ctx in `verify.py`. Task workers commit
  and never push (integration pushes); ci-fix workers push their own commits.
- Routing: a repo-local `routing:` block replaces the machine-wide `~/.worktrail/routing.yaml`
  wholesale (`_resolve_routing`), and `roles.<role>.tier` is validated against the tiers declared
  in the same block. The machine-wide file currently sets `roles.review.tier: t1-deep` (opus,
  high effort). Spec `model-tier-routing` requires provider/model intent to live in exactly one
  machine-wide file.
- `ruff` is not in the `dev` extra; CI installs it separately. It is installed on this machine.
- Policy int keys are validated in `load_policy()` by the `triage_keep_limit` pattern: non-bool
  int within range, else default plus a `_meta.warnings` entry.

## Goals / Non-Goals

**Goals:**
- Every rejection or fast path is deterministic code, with prompt text only as the first line of
  defense (matches the existing "prompt rule + deterministic backstop" posture in `verify.py`).
- Each new policy key is read with `.get(key, default)` at its consumer so the policy task and
  its consumers can land in any order.
- No change to journal replay for journals written before this change.

**Non-Goals:**
- A per-group `review_mode`. It moves review from the task loop into `integrate.py`'s group PR
  flow, a different module and failure surface; with the small-diff skip and the faster review
  tier in place it is not needed to hit the throughput target. Deferred, not designed here.
- Changing `MAX_REVIEW_RETRIES`, `plan_groups()`, or triage/fold logic.
- Re-checking plan shape on a resumed run with a pinned plan.

## Decisions

**D1. `review:` becomes a `tasks.md` continuation line, parsed exactly like `files:`.**
`schema.py` gains `REVIEW_RE` in the same continuation window (first declaration wins, duplicate
and empty values warn), `ParsedTask.review`, and `source.py` copies it onto the task dict. No
compile change is needed: `runplan.apply_to_tasks()` already keeps a task's own `review` over the
plan's. Alternative rejected: reuse the `[docs]` kind tag for config tasks. `docs` also changes
`kind`, which other code reads (dashboard, plan audit), and it misdescribes a policy-key edit.

**D2. Plan-shape problems are raised inside `compile_run_plan()`, not checked by each caller.**
`parallelism.py` gains `shape_problems(merged, repo, policy) -> list[str]` implementing the three
rules; `compile_run_plan()` merges the settled plan (seeded, cached, or freshly compiled) with
`apply_to_tasks`, calls it, and raises `PlanShapeError(problems)` when non-empty, after the plan
has been cached (the cache is content-addressed, so the next attempt re-checks the same plan and
fails the same way). `compile.main()` catches it, prints each line to stderr in both output modes,
returns 1, and never reaches `write_marker()`, so `worktrail-check-compile-markers` in CI also
sees the change as uncompiled. `live.py` needs no edit: `apply_run_plan` catches only `OSError`,
so the error propagates and `full-real` stops before any worktree exists; precheck's
`_planned_tasks_without_llm` propagates it the same way, which is the right answer for a
read-only "can this run?" step. `format_warning()` and the `SERIAL_WARN_*` constants are
removed; the summary line stays.
Rule details:
- Serial: `critical_path > max(width, N)` on the fan-out profile. The line lists the ids of one
  longest chain (walk `compute_levels` back from a max-level task through its deps).
- Same-file chain: walk dependent runs where each task's `files` is exactly one path and equal
  to its predecessor's; a run longer than K is a problem naming the ids and the file.
- Missing test scope: task kind not in `TAIL_KINDS` and not `docs`, at least one file with a
  `src/` prefix, no file with a `tests/` prefix, and `repo.glob("tests/**/test_<stem>*.py")`
  non-empty for some declared `src/` file's stem. The line names the first matching test file.
  A new module with no counterpart passes; a task that declares a `tests/` path passes.
Thresholds come from `load_policy(repo)` keys `compile_max_critical_path_over_width` and
`compile_max_same_file_chain`, read with default 2. Alternative rejected: keep them warnings and
have the pipeline grep stderr. Warnings were already there and nothing acted on them.

**D3. Small-diff review skip sits beside `_review_exempt` in `drive()`, first review only.**
Condition: `review_skip_max_diff_lines > 0`, `task["retry_count"] == 0`, the implement report
that produced the current head had `status: success` and `tests: passed`, and
`git diff --numstat <base_commit>..HEAD` in the task worktree, summing added+removed over paths
that are not test files, is strictly under the threshold. Test file = any path component
`tests` or `test`, or a basename matching `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`.
Binary rows (`-`) count 0. The fast path writes a review-role journal entry with
`review_status: skipped-small-diff` and `notes: "review skipped: <n> non-test diff lines <
<threshold>"`. `dispatch.transition()` accepts `SKIPPED-SMALL-DIFF` as a pass (this is the one
`dispatch.py` change outside prompts). A review after a fix always spawns: the fix was written
against a failed verdict, and skipping it would pass an unreviewed correction. Policy is read
via `load_policy(repo).get("review_skip_max_diff_lines", 0)` next to `_default_smoke_cmd`'s
existing load. If `dispatch.py` lands without `live.py`, nothing changes; if `live.py` lands
first, a replayed skip entry raises in `transition()`, the replay loop skips it, and `drive()`
re-evaluates the same diff deterministically and skips again. The tail task verifies both orders
are not needed by verifying the merged result.

**D4. The faster review tier is an operator change to `~/.worktrail/routing.yaml`, not a repo
policy edit.** A repo-local `routing:` block would have to redeclare every target and tier to
make `roles.review.tier` validate, and `model-tier-routing` forbids a second file of model intent.
The migration step below sets `roles.review.tier: t2-build` (sonnet, medium) in the machine-wide
file, keeping `prefer: claude-sub` and `independent: true`. Nothing in this change's tasks edits
that file.

**D5. Scope escalation triggers on the report content, refunds the strike, and blocks
quarantine while pending.** `_scope_escalation_files()` drops the `status == "failed"`
precondition; the caller in `drive()` invokes it for `ROLE_REVIEW` reports with
`review_status == "FAILED"` and for every `ROLE_FIX` report. The once-only flag, existing-file
check, outside-current-scope filter, and in-flight collision rule are unchanged. When it fires
on a review report, `drive()` records the pre-transition `retry_count`, lets `_commit_step()`
apply the transition, then restores `retry_count`, forces `status = "fixing"` (overriding an
`escalated` result on the final strike), sets `task["_scope_pending"] = True`, and the next fix
dispatch clears it. `_scope_pending` is checked wherever `drive()` or the pipeline scheduler
would move a task to `escalated`/`failed`; while set, the task stays in `fixing`. Journal: the
triggering entry carries `scope_escalated: true` and `scope_escalated_files` (the new documented
key); `reconcile_from_journal` reads `scope_escalated_files`, falling back to `scope_added_files`
for older journals, and its existing forced-`fixing` replay covers review entries too. The
reviewer prompt gains: "If the fix requires changing files outside Scope (for example an existing
test that asserts the old behavior), list each as a repo-relative path in `missing_context`,
never only in `notes`." The fixer prompt gains the equivalent for a declined finding, with
`status: failed`. Alternative rejected: let the reviewer pass a red suite when the failure is an
intended contract change. That trades a mechanical rule for a judgment call the evidence shows
reviewers do not make consistently.

**D6. `pre_commit_cmd`: prompt rule plus a deterministic amend, task-level only.**
`build_worker_prompt` (implement, fix) and `build_group_prompt` (ci-fix) append a hard rule when
`ctx.get("pre_commit_cmd")` is set: run the exact command from the worktree root immediately
before every `git commit` and stage what it changes. `live.py` threads the value from policy
into the task ctx; `verify.py` threads it into the ci-fix ctx. The backstop lives in `live.py`:
after an implement or fix report with a `head_sha`, run the command (`shell=True`, `cwd=wt`,
bounded timeout) and read `git status --porcelain`; paths within the task's `files` (including
escalated ones) are `git add`ed and folded in with `git commit --amend --no-edit`, and
`rep["head_sha"]` is refreshed before `_commit_step()` journals it; any other modified tracked
path is restored with `git checkout --` and listed on the entry as `pre_commit_restored`; a
non-zero exit or timeout is recorded as `pre_commit_error` and does not fail the task, because
the integrate smoke command and CI still gate it. ci-fix workers get the prompt rule only: they
push their own commits, so an orchestrator amend would rewrite a pushed branch.
Alternative rejected: a git pre-commit hook installed into each worktree. Workers run under
several harnesses and `core.hooksPath` state is invisible in the journal.

**D7. repo-init seeding is workflow-driven and write-once.** `repo_init.py` scans
`.github/workflows/*.yml|yaml` `run:` lines for `ruff`, `oxlint`, and `prettier` and builds the
command from the detected set in that order (`ruff check . --fix && ruff format .`,
`npx oxlint --fix .`, `npx prettier --write .`). `default_policy_yaml()` gains a
`pre_commit_cmd` line only when the set is non-empty; the existing "policy file already exists →
skipped" branch is untouched.

**D8. This repo's policy and dev extras.** `.worktrail/policy.yaml` sets `pre_commit_cmd`,
appends `ruff check . && ruff format --check .` to `integrate_smoke_cmd`, and sets
`review_skip_max_diff_lines: 40`. `ruff` is added to the `dev` extra so `dev-install.sh` provides
the binary the smoke command now needs; CI's separate `pip install ... ruff` keeps working.

**D9. This change's own `tasks.md` follows the rules it introduces.** One task per module,
tests co-scoped, `review: skip` only on the config task, one `[e2e]` tail, no dependency edges.
`live.py` and `verify.py` are owned by one task; `dispatch.py` by another; the two contracts
between them (`SKIPPED-SMALL-DIFF` acceptance, `pre_commit_cmd` in ctx) are tolerant of either
landing first as described in D3 and D6.

## Risks / Trade-offs

- [Active changes whose plans are serial now fail to compile] → The problem line names the ids
  and the remedy; thresholds are policy keys; the check is skipped for pinned resumes so no
  in-flight run is affected.
- [Missing-test-scope rule flags a legitimate source-only task] → Only fires when a matching
  `tests/**/test_<stem>*.py` already exists; `[docs]`-kind tasks are exempt; the remedy is one
  path added to `files:`.
- [A small wrong diff bypasses review] → Bounded by the threshold, the tests-passed requirement,
  first-review-only, the integrate smoke command, and CI. Repos opt in explicitly.
- [Formatter rewrites files outside the task scope] → Restored, never committed, listed on the
  journal entry.
- [Escalation refund lets a task loop longer than three strikes] → At most one refund per task
  (once-only flag), so the ceiling rises by exactly one fix for escalated tasks only.
- [Reviewer lists a path that is not a file or collides with an in-flight task] → Existing
  validation drops it and the report is applied as an ordinary result, as today.

## Migration Plan

1. Merge; no journal migration (new keys are additive, replay falls back to legacy names).
2. Operator step on this machine: set `roles.review.tier: t2-build` in
   `~/.worktrail/routing.yaml`, then `worktrail-routing --check`.
3. Run `./scripts/dev-install.sh` from the canonical checkout to pick up `ruff` in the dev extra.
4. Rollback: revert the PR; a policy file carrying the new keys loads with `unknown_keys`
   warnings and no behavior change.
