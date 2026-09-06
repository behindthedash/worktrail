## 1. Pipeline-owned scope review and failure detail

- [x] 1.1 In `src/worktrail/router/land_pr.py`: extend `_run_record_main` to also capture
      stderr and, on `SystemExit` with a string code, keep that string, returning
      `(exit_code, stdout, detail)`. Have `land_pr` remember whether `_ensure_run_record`
      started the record (`pipeline_owns_record = request.run is None and run_path is not
      None`) and pass it to `_finish_or_checkpoint`, which (non-checkpoint mode only) first
      issues `run_record.main(["scope-review", run_path, "--item", request_summary,
      "--status", "complete", "--evidence", "<commit sha> on <branch> -> <pr_url>"])` when the
      flag is set, then `finish`; it returns `(ok, detail)` and fails the same way for a
      failed append as for a failed finish. Set `LandOutcome.detail` to that detail on each of
      the three "run record could not be completed" ceiling outcomes, leaving their
      `merge_result` strings unchanged. In `tests/router/test_land_pr.py` add: `run=None`
      landings on the merged, blocked, and open branches each issue exactly one `scope-review`
      call before `finish`; checkpoint mode issues none; a caller-supplied `run` issues none;
      a `RunRecordSpy` whose `finish` raises `SystemExit("scope_completeness_gate: ...")`
      yields `outcome=ceiling` with that text in `detail`.
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr.py
      (Requirements: Run record is completed with a real state)

- [x] 1.2 In `tests/router/test_land_pr_integration.py` add a test that lands with
      `run=None` against the real `run_record` module (`WORKTRAIL_HOME` pointed at a temp dir so the record lands under `<tmp>/runs/`,
      the file's existing fake-`gh` repo fixture, CI watch stubbed to all-pass) and asserts the written record has
      `status=done`, `final_status=completed_pr_open`, `pull_request` set, and a single
      `scope_review` entry of the form `complete | <request_summary> | ...` naming the PR URL.
      files: tests/router/test_land_pr_integration.py
      (Requirements: Run record is completed with a real state)

## 2. Risk-label correction without a working directory

- [x] 2.1 In `src/worktrail/router/pr_labels.py`'s `_run_gh_cmd`, pass
      `cwd=repo if repo and Path(repo).is_dir() else None`, leaving every other argument and
      the transient-TLS retry loop unchanged. In `tests/router/test_pr_labels.py` add:
      `ensure_pr_risk_label` with a nonexistent `repo` path and a full PR URL adds the label
      and the fake runner records `cwd=None` for both the view and the api call; with an
      existing directory the runner records `cwd=<repo>`; a bare PR number with a nonexistent
      repo still returns `None` with the existing "could not resolve" warning. Add one
      `tests/router/test_run_record.py` case: `finish` on a record whose `repository` was
      removed after creation applies the correction and prints no "risk-label correction
      failed" warning.
      files: src/worktrail/router/pr_labels.py, tests/router/test_pr_labels.py, tests/router/test_run_record.py
      (Requirements: Risk-label correction survives a torn-down landing repository)

## 3. Triage apply path regression

- [x] 3.1 In `tests/workqueue/test_queue_triage.py`, alongside the existing
      `_worktree_pr_close` / apply coverage, add a test that patches `queue_triage.land_pr`
      to capture its `LandRequest` and return a `landed` outcome with
      `final_status="completed_pr_open"`, then asserts the captured request has `run=None`
      and `request_summary == f"queue-triage {verdict} {brief_id}"`, and that the returned
      entry's `landing.final_status` is `completed_pr_open` (not `failed_recoverable`).
      files: tests/workqueue/test_queue_triage.py
      (Requirements: Run record is completed with a real state)

## 4. Verification

- [x] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass;
      depends on 1.1, 1.2, 2.1, 3.1.
