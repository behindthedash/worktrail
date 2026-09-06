## 1. Resume fast path in `land_pr()` (`land-pr-resume-fast-path`)

- [ ] 1.1 Implement requirements: Already-pushed commit with an existing PR
      skips the pre-PR steps; Resume probe failures fall back to the full
      pipeline; A merged or closed pull request is never sent to PR
      creation. In `src/worktrail/router/land_pr.py`, add a module-level
      helper (e.g. `_resume_state(repo, request, runner)`) that returns the
      resume context or `None`, and call it in `land_pr()` after the route
      validation and before `_commit_pending`. The helper checks, in order
      and declining (returning `None`) on any nonzero exit / timeout /
      unparseable output: `git status --porcelain` is clean;
      `_current_branch()` resolves; `_push_target()`; `git ls-remote
      <remote> refs/heads/<branch>` sha equals `git rev-parse HEAD`; `gh pr
      view <branch> --json url,number,state` (adding `-R <base_slug>` when
      `_push_target()` returned a slug) parses to a PR. On a hit,
      `land_pr()` skips steps 1-4: for state `OPEN`, compute labels with
      `pre_pr_gate.resolve_pr_labels(repo, load_policy(repo), request.risk,
      list(request.gates), request.base_branch, route=request.route or
      None)`, render the body via `render_pr_body()` with `gate_evidence`
      stating the gate was not re-run because the commit is already pushed,
      and continue into the existing
      `open_or_update_pull_request(...)`/`_ensure_run_record`/`_watch_ci`
      sequence unchanged; for `MERGED`, call `_ensure_run_record`, `run_record
      set ... pull_request`, then `_finish_or_checkpoint(...,
      "completed_and_merged", ...)` and return `LandOutcome(outcome="landed",
      final_status="completed_and_merged", ...)` without reaching
      `open_or_update_pull_request`; for `CLOSED` (unmerged), return
      `LandOutcome(outcome="ceiling", refused_step="pr_closed", ...)` with
      the PR URL in `detail`. Update the module docstring's ordered-step
      list to name this as step 0 and state its decline-on-anything-unclear
      posture. In the same task add `tests/router/test_land_pr_resume.py`
      following the existing fake-runner conventions in
      `tests/router/test_land_pr.py`, covering: (a) clean tree + matching
      remote tip + OPEN PR performs no `git commit`, no `git push` and no
      `preflight.main` call, yet still updates the PR and watches CI; (b)
      MERGED PR yields `outcome="landed"` /
      `final_status="completed_and_merged"` and issues no `gh pr create`;
      (c) CLOSED unmerged PR yields `outcome="ceiling"` with
      `refused_step="pr_closed"` and issues no `gh pr create`; (d) a dirty
      tree, (e) a remote tip differing from `HEAD`, (f) no PR for the
      branch, and (g) a failing `ls-remote` or failing/unparseable `gh pr
      view` each decline the fast path and run the full pipeline.

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_land_pr.py
      tests/router/test_land_pr_integration.py
      tests/router/test_land_pr_push_refusal.py
      tests/router/test_land_pr_resume.py` and `PYTHONPATH=src pytest -q
      tests/router` and confirm both are green, then run `python3 -m
      worktrail.orchestrator.orchestrate check`. Verification-only — no file
      changes expected.
