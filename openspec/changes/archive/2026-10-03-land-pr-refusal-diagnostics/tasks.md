## 1. Refusals carry the failed step's output

- [x] 1.1 Give every locally-checkable refusal in the PR-landing pipeline a detail that
      identifies its cause, restoring conformance with the capability's *Refusal leaves the
      remote untouched* requirement, which demands a refused outcome naming the failed step
      together with its output.
      In `src/worktrail/router/land_pr.py`:
      (a) add a private capture helper that runs `preflight.main(argv)` in-process under
      `redirect_stdout`/`redirect_stderr`, mirroring `_run_record_main`'s shape — a string
      `SystemExit` code becomes the detail, otherwise the captured stderr, else the captured
      stdout, else `""` — returning `(exit_code, detail)`;
      (b) change `_run_preflight_and_labels` to return `(refused_step, labels, detail)` and
      populate `detail` at all four refusal points: `SystemExit` from an invalid `--risk`, a
      non-zero gate exit, an unreadable pass marker, and a stale marker state. A denial must
      carry the gate's own output whether the gate wrote it to stdout or to stderr. Update the
      function docstring's return contract and the module docstring's step-3 line;
      (c) thread that detail into `LandOutcome(detail=...)` at the `land_pr()` preflight
      refusal site, matching the compile-marker and push sites immediately around it;
      (d) make `_commit_pending`'s four refusal causes distinguishable in its returned detail —
      failed `git status`, no `commit_message` supplied, failed `git add`, and failed
      `git commit` — quoting the failing subcommand's stderr (falling back to stdout) for the
      three git failures, matching `_push`'s detail composition. Keep the returned step name
      `"dirty_tree"` unchanged, and keep the clean-tree and commit-success paths byte-for-byte
      identical;
      (e) thread that detail into `LandOutcome(detail=...)` at the `land_pr()` dirty-tree
      refusal site, so every `refused` return in `land_pr()` populates `detail`.
      A reproduction against base 36af09a1 established that the preflight denial reason is
      written to **stderr** (353 chars, `PRE-PR GATE: FAIL — unconfigured (default-deny)`),
      with captured stdout empty, so a stdout-only capture is insufficient.
      In `tests/router/test_land_pr.py`: update the existing `_run_preflight_and_labels` unit
      tests for the three-element return and strengthen the refusal tests to assert `detail` is
      populated; add a test exercising the REAL `_run_preflight_and_labels` (preflight not
      mocked on the failing path) against a worktree whose pre-PR gate denies, asserting the
      detail contains the gate's own failure text — the case
      `test_preflight_failure_refuses_and_never_pushes` cannot catch, because it mocks the
      very helper under test; add tests over the real `_commit_pending` asserting the detail
      distinguishes the status-failure, missing-message, add-failure and commit-failure causes;
      and add a `land_pr()`-level test asserting that a refusal reached through the real
      `_commit_pending` and one reached through the real `_run_preflight_and_labels` both
      surface a non-empty `LandOutcome.detail`.
      In `tests/router/test_pr_creation_callsite_enforcement_coverage.py`: update the direct
      `_run_preflight_and_labels` call sites (around lines 157, 574 and 593) and the two
      source-shape assertions that pin the `labels,` threading (around lines 187 and 290) for
      the three-element return.
      Then verify: `PYTHONPATH=src python3.14 -m pytest -q` (targeted
      `pytest tests/router/test_land_pr.py` first) and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check` are green, as are
      `python3.14 scripts/ci/ruff_pinned.py check .`,
      `python3.14 scripts/ci/ruff_pinned.py format --check .` and
      `python3.14 scripts/ci/check_shebang_exec_bits.py`. (`python3.14` is a PATH lookup;
      `export PATH="$PWD/.venv/bin:$PATH"` if it resolves to an interpreter without the dev
      extras.) Confirm with `rg "_run_preflight_and_labels\(" -A3` across `src/` and `tests/`
      that every call site is updated for the new return shape.
      Known contract: `refused_step: "dirty_tree"` is documented and guarded by
      `tests/router/test_documented_land_pr_invocations.py`; this change must not alter it.
      (Requirement: Refusal leaves the remote untouched.)
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr.py, tests/router/test_pr_creation_callsite_enforcement_coverage.py

## Notes

- When this change lands, the in-flight brief
  `20261003-162917-dirty-tree-conflates-five-distinct` (already `picked`, batched behind
  `20261003-163000-preflight-refusal-lacks-detail-in` as its primary) is superseded by it —
  mark that brief done rather than reimplementing it, since its two allowed fixes are exactly
  what task 1.1 delivers.
- `pr-landing-pipeline` under `openspec/specs/` is updated only by archive/sync, never edited
  by hand from this change directory.
