## 1. Shared pipeline module — prerequisite for groups 2–5 and 12 (OpenSpec has no cross-group edge; dispatch this group first)

- [ ] 1.1 Create `src/worktrail/router/land_pr.py` and register `worktrail-land-pr =
      "worktrail.router.land_pr:main"` under `[project.scripts]` in `pyproject.toml`.
      The module holds `LandRequest`/`LandOutcome` frozen dataclasses,
      `land_pr(request) -> LandOutcome`, the private step functions
      (`_commit_pending`, `_ensure_compile_markers`, `_run_preflight_and_labels`,
      `_push`, `open_or_update_pull_request`, `_ensure_run_record`, `_watch_ci`,
      `_merge_state_guard`, `_review_thread_gate`, `_finish_or_checkpoint`),
      `render_pr_body()` (standard sections per `routes.md`'s PR template: summary,
      route, spec lineage, pre-PR gate evidence, risk, labels, auto-merge
      recommendation), and `main()` (`--repo --base --title --summary/--summary-file
      [--run] --route [--risk] [--gates] [--commit-message] [--checkpoint]
      [--watch-timeout] --json`; exit 0 landed / 2 refused / 3 code_defect or
      review_threads_blocking / 4 ceiling). The module docstring is the authoritative
      ordered step list (design.md D2) and names the incident (PR #902) each step
      closes. Reuse `check_compile_markers.changed_change_dirs`/`check_marker`,
      `conductor_compile.main`, `preflight`'s in-process gate + `read_marker`,
      `pr_labels._run_gh_cmd`/`ensure_pr_risk_label`/`ensure_pr_no_automerge_label`,
      `check_review_threads.check`, and `run_record`'s `cmd_start`/`cmd_set`/
      `cmd_append`/`cmd_finish` semantics — no second implementation of any of them.
      Inject `runner` with a live `subprocess.run` default. (Requirement: Compile
      marker is current before anything is pushed; Requirement: Labels are computed by
      the preflight gate and applied on create and update; Requirement: Standard PR
      body; Requirement: CI watch runs to a classified terminal outcome; Requirement:
      Merge-state guard and review-thread gate before completion; Requirement: Run
      record is completed with a real state; Requirement: Refusal leaves the remote
      untouched)
      files: src/worktrail/router/land_pr.py, pyproject.toml
- [ ] 1.2 Tests for the pipeline. `tests/router/test_land_pr.py` (injected `FakeRun`-style
      runner scripting every `git`/`gh` reply): dirty tree without `commit_message` →
      `refused`/`dirty_tree`, no push recorded; compile gaps → `refused`/`compile_marker`,
      no push; preflight non-zero → `refused`/`preflight`; existing OPEN PR → no
      `gh pr create`, `ensure_*_label` called; transient check name → `gh run rerun
      --failed`, `ci_patch_iterations` unchanged; non-transient failure →
      `code_defect`, iteration incremented, no `finish`; fifth defect → `finish
      failed_recoverable`; `state: MERGED` → `finish completed_and_merged`;
      `mergeStateStatus: BLOCKED` with a CANCELLED/SUCCESS pair → `gh run rerun <id>`
      ≤ 2; review threads `blocking: true` → `review_threads_blocking`, no `finish`;
      auto-merge armed → `completed_pr_open` naming the mechanism; `checkpoint=True`
      on all-pass → `append … decisions`, no `finish`; `run=None` → a run record is
      started and returned; `render_pr_body` always carries the gate-evidence, risk,
      labels, and auto-merge sections. `tests/router/test_land_pr_integration.py`
      (real `git` + bare `origin`, `tests/orchestrator/lifecycle/fake_gh.py` first on
      `PATH` with `$GH_FAKE_STATE`, an OpenSpec change whose `tasks.md` differs from
      `origin/main`): (a) no marker and (b) stale marker with the compile unable to
      pass (tasks without `files:`, no model) → `git ls-remote origin <branch>` empty
      and `$GH_FAKE_STATE.prs` empty after `refused`/`compile_marker`; (c) tasks
      declaring `files:` → marker committed, branch pushed, one PR in fake state
      carrying the preflight-recorded labels. (Requirement: Compile marker is current
      before anything is pushed; Requirement: Refusal leaves the remote untouched;
      Requirement: CI watch runs to a classified terminal outcome; Requirement:
      Merge-state guard and review-thread gate before completion; Requirement: Run
      record is completed with a real state; Requirement: Standard PR body)
      files: tests/router/test_land_pr.py, tests/router/test_land_pr_integration.py

## 2. Migrate queue-triage fold/propose (after group 1)

- [ ] 2.1 In `src/worktrail/workqueue/queue_triage.py` `_worktree_pr_close()`, replace the
      commit / `git push` / `_refresh_pr_labels` / `gh pr create` block with one
      `land_pr(LandRequest(repo=worktree_dir, base_branch=base_branch,
      title=_planned_fold_propose_pr_title(v), summary=pr_body, route="C", risk="low",
      run=None, request_summary=f"queue-triage {v.verdict} {v.brief_id}",
      commit_message=commit_message, watch_timeout_s=…))` call after `openspec
      validate`; treat `outcome == "refused"` as the existing pre-PR error path (brief
      untouched, branch reported); on any outcome with a `pr_url` claim+close the brief
      as today and add a `landing` sub-dict (`outcome`, `run`, `final_status`,
      `merge_result`, `failing_checks`, `worktree`) to the result; keep the worktree on
      `code_defect`/`review_threads_blocking` and report its path; drop the
      `from ..orchestrator.integrate import _refresh_pr_labels` import; update the
      three docstrings to "lands via `router.land_pr`". In
      `tests/workqueue/test_queue_triage.py` replace the `gh pr create` scripting in the
      fold/propose dispatcher stubs with `mock.patch("worktrail.workqueue.
      queue_triage.land_pr")` returning a `LandOutcome`; assert one call per apply with
      `route="C"`, the planned title, and `commit_message`; assert `refused` leaves the
      brief queued with `status: error` and the branch name; assert `landed` closes the
      brief with the PR URL and `landing.final_status`; assert `code_defect` closes the
      brief and leaves the worktree on disk with `landing.worktree` set. (Requirement:
      Every PR-opening path lands through the shared pipeline; Requirement: Fold and
      propose are applied as a pull request, fail-closed)
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 3. Migrate close-stale OpenSpec (after group 1)

- [ ] 3.1 In `src/worktrail/router/close_stale_openspec.py`, after a successful
      `flip_and_archive`, `main()` calls `land_pr(LandRequest(repo=worktree,
      base_branch=<--base>, title=f"chore({change_id}): close stale bookkeeping",
      summary=…, route="E", risk="low", run=<--run>, commit_message=…))` and merges
      the `LandOutcome` into its JSON output; add required `--base` and `--run`
      arguments; rewrite the module docstring's "deliberately does NOT commit, push,
      open a PR" paragraph to say landing now goes through the shared pipeline and why
      the old reason no longer applies; exit code follows the pipeline's mapping. In
      `tests/router/test_close_stale_openspec.py` patch
      `worktrail.router.close_stale_openspec.land_pr`; assert `main()` invokes it once
      after a successful flip+archive with `route="E"`, the worktree path, `--base`, and
      `--run`; assert it is not invoked when `flip_and_archive` reports an error; assert
      the JSON output carries the landing outcome. (Requirement: Every PR-opening path
      lands through the shared pipeline)
      files: src/worktrail/router/close_stale_openspec.py, tests/router/test_close_stale_openspec.py

## 4. Migrate drain remediations (after group 1)

- [ ] 4.1 In `src/worktrail/drain/drain.py`, replace `_open_sync_pending_pr`,
      `_open_stale_bookkeeping_pr`, and `_open_openspec_archive_pr` with one
      `_land_remediation_pr(wt, repo_name, spec_id, base, title, summary, timeout)` that
      calls `land_pr(LandRequest(repo=wt, base_branch=base, route="E", risk="low",
      run=None, request_summary=f"drain {title}", watch_timeout_s=timeout, …))`,
      returns the PR URL on `landed`, and raises `RuntimeError` (caught by the sweep
      engine's per-finding try/except, as today) on any other outcome with the outcome
      detail in the message; keep the three callers' existing-PR re-detection; drop the
      `_refresh_pr_labels` import and the three hand-rolled `["gh","pr","create",…]`
      lists. In `tests/drain/test_drain.py` patch `worktrail.drain.drain.land_pr`; for
      each of the three actions assert one call with `route="E"`, `risk="low"`, the
      action's `timeout` as `watch_timeout_s`, and the action's title; assert a
      non-`landed` outcome raises and is recorded as that finding's failure; remove the
      dead `gh pr create` stubs. (Requirement: Every PR-opening path lands through the
      shared pipeline)
      files: src/worktrail/drain/drain.py, tests/drain/test_drain.py

## 5. Migrate orchestrator group-PR open step (after group 1)

- [ ] 5.1 In `src/worktrail/orchestrator/integrate.py`'s group-PR creation path, replace
      `_refresh_pr_labels(...)`, `_pr_label_args(...)`, the inline
      `["gh","pr","create",…]` list, and its `_run_gh_with_retry` call with
      `land_pr.open_or_update_pull_request(repo, pr_base, gb, title, body, risk=<from
      pr_labels seed>, gates=gates, route=route, runner=…)`; keep the
      quarantine-on-failure guard, journal writes, and everything in `verify.py`
      unchanged; delete `_refresh_pr_labels`, `_pr_label_args`, and
      `_extract_risk_from_labels` if no importer remains (design.md D6). In
      `tests/orchestrator/test_integrate.py` patch
      `worktrail.router.land_pr.open_or_update_pull_request`; assert the group path
      calls it once per new group PR with the group's `pr_base`, branch, and route and
      never calls `land_pr.land_pr`; keep the "gh pr create failure quarantines the
      group" test green by returning the failure shape from the patched step.
      (Requirement: Every PR-opening path lands through the shared pipeline)
      files: src/worktrail/orchestrator/integrate.py, tests/orchestrator/test_integrate.py

## 6. Prose: sdd-workflow Phase 8

- [ ] 6.1 In `skills/worktrail-sdd-workflow/SKILL.md` Phase 8, keep the
      scope-completeness gate paragraph; replace the "Mandatory pre-PR test gate",
      "Automerge labels", and "For every PR produced" 1–4 sections with one
      `worktrail-land-pr` command block (flags per design.md D8), its exit-code table,
      and two follow-ups: on `code_defect` repair per
      `../worktrail-go/references/ci-watch-loop.md` case 3 and re-run the same command;
      on `review_threads_blocking` act per its review-thread gate and re-run. Keep the
      "Only routes with non-PR completion states…" paragraph. Every `worktrail-*`
      command named must be a real entry point (`tests/test_plugin_surface.py`).
      (Requirement: Every PR-opening path lands through the shared pipeline)
      files: skills/worktrail-sdd-workflow/SKILL.md

## 7. Prose: Route C spec-PR checkpoint

- [x] 7.1 In `skills/worktrail-go/references/routes.md` §C, replace "push `spec/$SPEC_ID`
      and open a docs-only PR … CI-watch the spec PR now" with `worktrail-land-pr --repo
      "$WT" --base "$BASE" --run "$RUN" --route C --risk low --checkpoint …`, stating
      that checkpoint mode appends the outcome as a decision and returns control; keep
      the inline-D transition text. (Requirement: Run record is completed with a real
      state)
      files: skills/worktrail-go/references/routes.md

## 8. Prose: worktrail-go intake gate, close-stale row, CI-watch paragraph

- [x] 8.1 In `skills/worktrail-go/SKILL.md`: (a) Phase 2 intake gate step 3 — replace
      "Report the resulting action-log entry … and STOP" with: run the apply with the
      Bash `timeout` parameter at 600000; report `pr_url` and `landing.outcome`; on
      `code_defect`/`review_threads_blocking` continue with `worktrail-land-pr` against
      `landing.run` from `landing.worktree` until a terminal outcome, then stop; still
      no Phase 3 claim/dispatch. (b) close-stale row — replace "Land the same PR through
      the normal Phase 8 flow (…) — never a hand-rolled `gh pr create`" with "pass
      `--base "$BASE" --run "$RUN"`; it lands the PR through the shared pipeline".
      (c) Phase 3 CI-watch paragraph — name `worktrail-land-pr` as the command that
      performs commit → push → PR → CI watch → `finish`. (Requirement: Interactive
      pickup of an intake brief triages it; Requirement: Every PR-opening path lands
      through the shared pipeline)
      files: skills/worktrail-go/SKILL.md

## 9. Prose: sync-PR subagent step

- [ ] 9.1 In `skills/worktrail-go/references/subagent-prompts.md`'s sync step, replace the
      `worktrail-pre-pr-gate --labels-only` + `gh pr create` block and Step 4b's `gh pr
      checks --watch` / `gh pr merge` with `worktrail-land-pr --repo "$SYNC_WT" --base
      "$BASE" --run "$RUN" --route E --risk low --checkpoint …`; keep the
      `SYNC_ALREADY_LANDED` pre-check and the teardown steps. (Requirement: Every
      PR-opening path lands through the shared pipeline)
      files: skills/worktrail-go/references/subagent-prompts.md

## 10. Prose: pipeline-details marker note

- [x] 10.1 In `skills/worktrail-sdd-workflow/references/pipeline-details.md` step 3 marker
      notes (both pipelines), state that `worktrail-land-pr` re-runs the compile and
      commits `.compile-ok` at landing, so the spec PR can no longer ship without it;
      leave the pre-launch uncommitted-output guard text unchanged. (Requirement:
      Compile marker is current before anything is pushed)
      files: skills/worktrail-sdd-workflow/references/pipeline-details.md

## 11. Prose: ci-watch-loop reference note

- [x] 11.1 In `skills/worktrail-go/references/ci-watch-loop.md`, add the top note from
      design.md D8 ("Implemented in code by `worktrail-land-pr` …; the agent's remaining
      steps are repairing a reported code defect and deciding case 4"); reframe case 4's
      opening sentence as "when `worktrail-land-pr` reports `code_defect` and the fix
      needs a product decision"; leave the rest unchanged. (Requirement: CI watch runs
      to a classified terminal outcome)
      files: skills/worktrail-go/references/ci-watch-loop.md

## 12. Enforcement collapse (after groups 2–5)

- [ ] 12.1 In `tests/router/test_pr_creation_callsite_enforcement_coverage.py`, collapse
      `CALLSITE_CONSUMERS` to `{"router/land_pr.py":
      _proves_land_pr_labels_come_from_preflight_marker, "orchestrator/live.py":
      <existing sandbox proof>}`; delete the `integrate`/`drain`/`queue_triage` proof
      functions and imports; the new proof asserts, via an injected runner, that the
      `--label` flags on the `gh pr create` argv equal the labels read from the
      preflight pass marker; add PR #902 to the module docstring's incident list.
      (Requirement: Every PR-opening path lands through the shared pipeline)
      files: tests/router/test_pr_creation_callsite_enforcement_coverage.py

## 13. Verification

- [ ] 13.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; confirm `tests/test_plugin_surface.py`
      accepts `worktrail-land-pr` everywhere the skills name it and
      `tests/router/test_skill_prose_enforcement_coverage.py` stays green after the
      prose edits; confirm `rg '"gh", "pr", "create"' src/` matches only
      `router/land_pr.py` and `orchestrator/live.py`.
