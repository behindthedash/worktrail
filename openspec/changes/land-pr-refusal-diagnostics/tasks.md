## 1. Preflight refusal detail

- [ ] 1.1 Add a private capture helper in `src/worktrail/router/land_pr.py` that runs
      `preflight.main(argv)` in-process under `redirect_stdout`/`redirect_stderr` (mirroring
      `_run_record_main`'s shape: a string `SystemExit` code becomes the detail, otherwise the
      captured stderr, else the captured stdout, else `""`), returning `(exit_code, detail)`.
- [ ] 1.2 Change `_run_preflight_and_labels` to return `(refused_step, labels, detail)` and
      populate `detail` at all four refusal points -- `SystemExit` from an invalid `--risk`,
      non-zero gate exit, unreadable pass marker, and stale marker state -- so a denial carries
      the gate's own output whether it was written to stdout or stderr. Update its docstring's
      return contract and the module docstring's step-3 line.
- [ ] 1.3 Thread the preflight detail into `LandOutcome(detail=...)` at the `land_pr()`
      preflight refusal site, matching the compile-marker and push sites immediately around it.

## 2. Dirty-tree refusal detail

- [ ] 2.1 Make `_commit_pending`'s four refusal causes distinguishable in its returned
      detail -- failed `git status`, no `commit_message` supplied, failed `git add`, failed
      `git commit` -- quoting the failing subcommand's stderr (falling back to stdout) for the
      three git failures, matching `_push`'s detail composition. Keep the step name
      `"dirty_tree"` unchanged and keep the clean-tree and commit paths byte-for-byte identical.
- [ ] 2.2 Thread that detail into `LandOutcome(detail=...)` at the `land_pr()` dirty-tree
      refusal site, so every `refused` return in `land_pr()` now populates `detail`.

## 3. Regression tests over the real functions

- [ ] 3.1 Update the five existing `_run_preflight_and_labels` unit tests in
      `tests/router/test_land_pr.py` and the three direct calls in
      `tests/router/test_pr_creation_callsite_enforcement_coverage.py` (lines ~157, ~574, ~593)
      for the three-element return, and strengthen those refusal tests to assert `detail` is
      populated.
- [ ] 3.2 Add a test exercising the REAL `_run_preflight_and_labels` (preflight NOT mocked on
      the failing path) against a worktree whose pre-PR gate denies, asserting the returned
      detail contains the gate's own failure text -- the case `test_preflight_failure_refuses_and_never_pushes`
      cannot catch because it mocks the helper itself.
- [ ] 3.3 Add tests over the real `_commit_pending` in `tests/router/test_land_pr.py`
      (`CommitPendingTests`) asserting the detail distinguishes the status-failure,
      missing-message, add-failure, and commit-failure causes and quotes git's stderr.
- [ ] 3.4 Add a `land_pr()`-level test asserting a refusal reached through the real
      `_commit_pending` and one through the real `_run_preflight_and_labels` both surface a
      non-empty `LandOutcome.detail`.

## 4. Verification

- [ ] 4.1 Run `PYTHONPATH=src python3.14 -m pytest` (targeted
      `pytest tests/router/test_land_pr.py` first) and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check` -- both green.
      Note which interpreter `python3.14` resolves to (`export PATH="$PWD/.venv/bin:$PATH"`
      from the canonical checkout if it lacks the dev extras).
- [ ] 4.2 Run the pinned-ruff lint and format check plus
      `python3.14 scripts/ci/check_shebang_exec_bits.py`.
- [ ] 4.3 `rg "_run_preflight_and_labels\(" -A3` across `src/` and `tests/` to confirm every
      call site is updated for the new return shape, and `openspec validate
      land-pr-refusal-diagnostics` passes.

## 5. Spec sync

- [ ] 5.1 Re-read the delta spec's *Refusal leaves the remote untouched* requirement against
      what was implemented and reconcile any wording drift; `pr-landing-pipeline` in
      `openspec/specs/` is updated only by archive/sync, never edited by hand here.

## Notes

- The step name `"refused_step": "dirty_tree"` is a documented contract guarded by
  `tests/router/test_documented_land_pr_invocations.py`; this change must not alter it or that
  guard will fail.
- When this change lands, the in-flight brief `20261003-162917-dirty-tree-conflates-five-distinct`
  (already `picked`, with `20261003-163000-preflight-refusal-lacks-detail-in` as its batch
  primary) is superseded by it -- mark that brief done rather than reimplementing it, since its
  two allowed fixes are exactly what sections 1 and 2 above deliver.
