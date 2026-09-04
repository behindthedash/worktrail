## 1. Terminal-failure bail-out and per-poll logging in the bounded external-merge wait

- [ ] 1.1 In `src/worktrail/orchestrator/verify.py`, `Verifier._wait_for_external_merge()`
      (currently lines 1271-1291), change only the body of the `for poll in
      range(self.max_polls)` loop, leaving `max_polls`, `poll_interval`,
      `poll_interval_max`, the `1.4**poll` backoff, the signature, both existing return
      shapes, and every other method in the file untouched. After the existing
      `state == "MERGED"` test (which must stay first, so an actually-merged PR is never
      turned into a quarantine — design.md Decision "Classify after the MERGED test"),
      classify the live required checks with the same idiom `_block_on_checks` already
      uses: `pending, failing = classify_checks(st.get("statusCheckRollup"),
      required=self._required_check_names())`. When `not pending and failing`, return
      `(False, <reason naming the failing checks>)` immediately, so the caller
      `_recheck_merged_before_quarantine` quarantines without consuming the remaining
      poll budget. Every other state — any `pending` (including "some failing, some still
      running"), and `not pending and not failing` (all green, merge imminent) — falls
      through to the existing `self.sleep(...)` and keeps waiting exactly as today. Before
      that sleep, emit one log line per iteration mirroring `_block_on_checks`'s own
      pattern (`    [{group['name']}] ...` naming what the wait is waiting on and
      `(poll {poll + 1})`). Do not arm or attempt any merge, do not add a policy key, and
      do not extract a helper shared with `_block_on_checks` (design.md Non-Goals). Add
      the regression tests to the existing `LiveMergeRecheckUnit` class in
      `tests/orchestrator/test_verify.py`, reusing its harness (`FakeRun`, `view(state=,
      rollup=, auto_merge_request=)`, the `GREEN`/`RED` rollup fixtures, `mk()`): (a) an
      armed `autoMergeRequest` on an OPEN PR with a terminally-failed rollup (`RED`)
      returns quarantine, the returned/logged reason names the failing check, and the
      number of `gh pr view` calls proves the poll budget was not consumed; (b) an armed
      `autoMergeRequest` with a still-pending rollup (a check with `status: "IN_PROGRESS"`
      / no conclusion) keeps waiting and still returns merged once a later view flips to
      MERGED — i.e. the legitimate wait is unshortened; (c) a MERGED view whose rollup is
      `RED` still returns merged, proving the MERGED test wins over the bail-out; (d) the
      wait emits a log line per poll; (e) `gh pr merge` is never invoked on any of these
      paths. (Requirement: Bounded wait for an externally-armed auto-merge before
      finalizing quarantine)
      files: src/worktrail/orchestrator/verify.py, tests/orchestrator/test_verify.py

## 2. Verification

- [ ] 2.1 [cleanup] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; confirm both are green including the new
      `LiveMergeRecheckUnit` tests, and run `openspec validate
      quarantine-live-merge-recheck-terminal-check-failure-bailout --strict`.
      Verification-only — no file changes expected.
