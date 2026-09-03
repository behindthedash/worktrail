## 1. Task authoring guidance

- [x] 1.1 In `skills/openspec-propose/SKILL.md`'s tasks-artifact step add three sub-bullets after
      the existing hot-file guidance, cross-referencing it rather than restating it: (a) one
      implementation task per module per phase sized for roughly 20-60 minutes, consecutive
      same-file steps folded into one task with sub-bullets, never a dependent chain; (b) an
      implementation task's `files:` MUST include every existing test file asserting behavior the
      task changes plus the new test file it adds, and implementation and tests are never split
      into separate tasks; (c) mechanical or docs-only tasks (config keys, prose, a single
      constant) carry an indented `review: skip` continuation line, and a task producing
      executable behavior never does. Update the closing guardrail line to re-check these rules.
      In `tests/test_plugin_surface.py` add a prose test asserting the skill text contains the
      sizing rule, the never-split co-scoping rule, and the `review: skip` rule. (Requirement:
      Implementation tasks are coarse and module-scoped; Requirement: Implementation tasks
      co-scope the tests they change; Requirement: Mechanical tasks opt out of review at
      authoring time)
      files: skills/openspec-propose/SKILL.md tests/test_plugin_surface.py

## 2. tasks.md review declaration

- [x] 2.1 In `src/worktrail/taskformats/openspec/schema.py` add `REVIEW_RE` matched in the same
      continuation window as `FILES_RE` (first declaration wins; a duplicate or an empty value
      appends a warning naming the task), a `review: str = ""` field on `ParsedTask`, and its
      population in `parse_tasks_md`; in `src/worktrail/taskformats/openspec/source.py` copy
      `review` onto the task dict when non-empty. Tests in
      `tests/taskformats/openspec/test_openspec_schema.py` (skip parsed; `files:` and `review:`
      coexist in either order; duplicate and empty values warn; a top-level `review:` line is not
      a declaration) and `tests/taskformats/openspec/test_openspec_source.py` (the task dict
      carries `review: "skip"` and `live._review_exempt` returns True for it). (Requirement:
      Inline review declaration parsing)
      files: src/worktrail/taskformats/openspec/schema.py src/worktrail/taskformats/openspec/source.py tests/taskformats/openspec/test_openspec_schema.py tests/taskformats/openspec/test_openspec_source.py

## 3. Compile plan-shape gate

- [ ] 3.1 In `src/worktrail/conductor/parallelism.py` add `shape_problems(merged, repo, policy)`
      implementing design D2's three rules (serial: critical path > max(width, N) naming one
      longest chain; same-file chain longer than K naming ids and file; implementation task with
      a `src/` path, no `tests/` path, and an existing `tests/**/test_<stem>*.py`, naming that
      test file), reading `compile_max_critical_path_over_width` and
      `compile_max_same_file_chain` from the policy dict with default 2; remove `format_warning`
      and the `SERIAL_WARN_*` constants and update the module docstring. In
      `src/worktrail/conductor/compile.py` add `PlanShapeError(RuntimeError)` carrying the
      problem lines, raise it from `compile_run_plan` after the settled plan is cached (seeded,
      cached, and compiled paths alike) using `runplan.apply_to_tasks` and `load_policy(repo)`,
      and in `main()` catch it, print each line to stderr in text and `--json` modes, return 1,
      and skip `write_marker`. Add fixtures `tests/fixtures/plan_shape/serial-group4.tasks.md`
      (run full-1788369246's group 4 shape: 4.1, 4.2, 4.4 chained on
      `src/worktrail/workqueue/queue_triage.py`, plus a `create_handoff.py` task with no test
      path) and `tests/fixtures/plan_shape/consolidated.tasks.md` (one task per module with tests
      co-scoped and an `[e2e]` tail). Tests in `tests/conductor/test_parallelism.py` (each rule
      fires with the expected ids, file, and test-file name; thresholds from policy; tail kinds
      and `docs` exempt; a new module with no test counterpart passes) and
      `tests/conductor/test_compile.py` (the serial fixture compiles with `--no-llm` to exit 1 and
      no marker in text and `--json` modes; the consolidated fixture exits 0; `compile_run_plan`
      raises `PlanShapeError` on a cache hit of a rejected plan). (Requirement: Serial plans are
      rejected at compile; Requirement: Same-file dependent chains are rejected at compile;
      Requirement: Implementation tasks without test scope are rejected when a test counterpart
      exists; Requirement: Plan-shape rejections propagate to every compile consumer)
      files: src/worktrail/conductor/parallelism.py src/worktrail/conductor/compile.py tests/conductor/test_parallelism.py tests/conductor/test_compile.py tests/fixtures/plan_shape/serial-group4.tasks.md tests/fixtures/plan_shape/consolidated.tasks.md

## 4. Orchestrator review fast path, scope escalation, pre-commit backstop

- [ ] 4.1 In `src/worktrail/orchestrator/live.py`: (a) small-diff skip per design D3 beside the
      `_review_exempt` fast path in both `drive()` bodies, gated on
      `review_skip_max_diff_lines` read via `load_policy(repo).get(..., 0)`, first review only,
      implement report `status: success` and `tests: passed`, non-test added+removed lines from
      `git diff --numstat` under the threshold, journaling `review_status: skipped-small-diff`;
      (b) scope escalation per design D5: drop the `status == "failed"` precondition from
      `_scope_escalation_files`, call it for FAILED review reports and all fix reports, restore
      the pre-transition `retry_count` and force `fixing` when a review triggers it, set and
      consume `_scope_pending` so a pending escalation is never moved to `escalated`/`failed`,
      write `scope_escalated_files` on the journal entry, and read it (falling back to
      `scope_added_files`) in `reconcile_from_journal`; (c) pre-commit backstop per design D6:
      after an implement or fix report with a `head_sha`, run `pre_commit_cmd` in the worktree,
      amend in-scope changes and refresh `head_sha`, restore out-of-scope tracked changes as
      `pre_commit_restored`, record a non-zero exit or timeout as `pre_commit_error`; thread
      `pre_commit_cmd` into the task worker ctx. In `src/worktrail/orchestrator/verify.py`
      thread `pre_commit_cmd` into the ci-fix group ctx. Tests: extend
      `tests/orchestrator/test_context_widening.py` (escalation from a reviewer FAILED report
      with paths; from a fixer `status: success` report; no fire on in-flight collision; never
      twice; strike refunded; third-strike review with paths returns to `fixing`; journal entry
      carries `scope_escalated_files`; resume from a review entry restores scope and `fixing`);
      new `tests/orchestrator/test_review_small_diff_skip.py` (threshold 0 never skips; small
      passing diff skips with the journal verdict; `tests: none` never skips; test-file lines are
      excluded from the count; post-fix review always spawns; resume from a skipped entry does
      not spawn); new `tests/orchestrator/test_pre_commit_cmd.py` (command runs before the
      journal entry; in-scope change amended and `head_sha` refreshed; clean tree leaves the
      commit unchanged; out-of-scope change restored and noted; non-zero exit noted, task not
      failed; unset key runs nothing); extend `tests/orchestrator/test_verify.py` (ci-fix ctx
      carries `pre_commit_cmd`). (Requirement: Small verified diffs skip the review spawn;
      Requirement: Small-diff skip is recorded and resumable; Requirement: A failed review with
      missing-context paths triggers scope escalation; Requirement: Escalation widens scope and
      re-dispatches the fix without a strike; Requirement: A task with a pending escalation is
      never quarantined; Requirement: Escalated files are journaled; Requirement: The
      orchestrator re-runs pre_commit_cmd after each task commit)
      files: src/worktrail/orchestrator/live.py src/worktrail/orchestrator/verify.py tests/orchestrator/test_context_widening.py tests/orchestrator/test_review_small_diff_skip.py tests/orchestrator/test_pre_commit_cmd.py tests/orchestrator/test_verify.py

## 5. Worker prompts and the review transition

- [x] 5.1 In `src/worktrail/orchestrator/dispatch.py`: add the `missing_context` path rule to the
      review action text and the fix action text (untouchable files listed as repo-relative
      paths, never only in `notes`; a declined out-of-scope finding is `status: failed` with the
      paths); add a hard rule to `build_worker_prompt` for implement and fix and to
      `build_group_prompt` for ci-fix when `ctx.get("pre_commit_cmd")` is set, naming the exact
      command as a before-every-commit step; make `transition()` accept
      `review_status: SKIPPED-SMALL-DIFF` as a passed review. Tests in
      `tests/orchestrator/test_dispatch.py`: reviewer and fixer prompts carry the
      `missing_context` rule; implement, fix, and ci-fix prompts carry the command when set and
      no pre-commit line when unset; `transition()` maps the skipped verdict to `cleaning` with
      retry count unchanged and still raises on an unknown verdict. (Requirement: Workers report
      untouchable files as missing context paths; Requirement: Workers run pre_commit_cmd before
      every commit)
      files: src/worktrail/orchestrator/dispatch.py tests/orchestrator/test_dispatch.py

## 6. Policy keys and repo-init seeding

- [ ] 6.1 In `src/worktrail/router/policy.py` add `compile_max_critical_path_over_width: 2`,
      `compile_max_same_file_chain: 2`, `review_skip_max_diff_lines: 0`, and
      `pre_commit_cmd: None` to `DEFAULTS` with comment blocks naming their consumers, extend the
      integer-validation block (the two compile keys >= 1, the review key >= 0, non-bool, else
      default with a warning) and force a non-string `pre_commit_cmd` to None with a warning. In
      `src/worktrail/onboarding/repo_init.py` add `detect_pre_commit_cmd(repo)` scanning
      `.github/workflows/*.yml|yaml` `run:` lines for `ruff`, `oxlint`, `prettier` per design D7
      and pass its result into `default_policy_yaml`, which emits a `pre_commit_cmd:` line only
      when non-empty; the existing policy file is never modified. Tests in
      `tests/router/test_policy.py` (each key's default; invalid, bool, and out-of-range values
      fall back with a warning; valid values kept; a string `pre_commit_cmd` kept) and
      `tests/onboarding/test_repo_init.py` (ruff-only workflow seeds the ruff command; ruff plus
      prettier joins with `&&`; no lint step omits the key; existing policy file untouched).
      (Requirement: Plan-shape thresholds are policy keys; Requirement: Review fast path is
      disabled by default; Requirement: pre_commit_cmd is a policy key; Requirement: repo-init
      seeds pre_commit_cmd from detected CI lint steps)
      files: src/worktrail/router/policy.py src/worktrail/onboarding/repo_init.py tests/router/test_policy.py tests/onboarding/test_repo_init.py

## 7. This repository's policy

- [x] 7.1 In `.worktrail/policy.yaml` set `pre_commit_cmd: "ruff check . --fix && ruff format ."`,
      append `&& ruff check . && ruff format --check .` to `integrate_smoke_cmd`, and set
      `review_skip_max_diff_lines: 40`, each with a one-line comment naming this change; in
      `pyproject.toml` add `ruff` to the `dev` extra so `dev-install.sh` provides the binary the
      smoke command needs. (Requirement: worktrail's own policy formats before commit and checks
      at integrate)
      files: .worktrail/policy.yaml pyproject.toml
      review: skip

## 8. Verification

- [ ] 8.1 [e2e] Run `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; run `worktrail-compile --no-llm` against a
      scratch OpenSpec change built from `tests/fixtures/plan_shape/serial-group4.tasks.md` and
      confirm exit 1 with the three problem lines and no marker, then against
      `consolidated.tasks.md` and confirm exit 0; run `worktrail-compile --no-llm` against this
      change's own directory and confirm exit 0 with critical path 1; run `ruff check . && ruff
      format --check .` clean; and confirm `load_policy` on this repo's `.worktrail/policy.yaml`
      reports no unknown keys.
