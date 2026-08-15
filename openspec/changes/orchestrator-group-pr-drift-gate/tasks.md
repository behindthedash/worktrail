## 1. Extract the drift checks into a shared helper

- [ ] 1.1 In `src/worktrail/router/pre_pr_gate.py`, add
  `run_drift_checks(repo: Path, policy: Dict[str, Any]) -> int` containing the four
  existing check blocks verbatim — `spec_sync_drift`, `check_changed_specs`
  (clarification-integrity), `check_dod_failures`, and the
  `_resolve_req_coverage_base_ref`-guarded `check_req_coverage_failures` — including their
  stderr reporting, in the same order, returning the matching `*_DRIFT_EXIT` constant on
  the first failure and 0 when all pass.
- [x] 1.2 Replace the inlined blocks in `main()` with a call to `run_drift_checks`, kept
  under the same `if not args.print_cmd:` guard, returning its value when non-zero.
  Verify `--print-cmd` behavior is unchanged.
- [ ] 1.3 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_pre_pr_gate.py` and confirm
  the existing suite (including every `--labels-only` test) passes with no test edits.

## 2. Add the `--checks-only` gate mode

Implements requirement: **A checks-only gate mode runs the drift checks without the test command**.

- [ ] 2.1 Add a `--checks-only` argparse flag to `main()` with help text stating it runs
  the four deterministic drift checks and does not run the policy test command.
- [ ] 2.2 Branch on it after `policy = load_policy(repo)` and after the `--labels-only`
  branch: call `run_drift_checks(repo, policy)` and return its value directly, so the mode
  never reaches `scope_review_failures`, `is_docs_only`, `resolve_cmd`, or the
  `subprocess.run(["bash","-c",cmd])` execution (design D2).
- [ ] 2.3 Update `pre_pr_gate.py`'s module docstring: add `--checks-only` to the Usage
  line, describe the mode and which checks it runs, and state explicitly that it skips
  `pre_pr_cmd`, the docs-only bypass, the unconfigured default-deny, and
  scope-completeness review, with the reason for each (design D2).
- [ ] 2.4 Add `tests/router/test_pre_pr_gate.py` coverage: `--checks-only` on a
  drift-free tree exits 0 and does **not** execute the configured `pre_pr_cmd` (assert via
  a sentinel command whose side effect would be observable); one test per drift class
  asserting its distinct exit code; a repo with no `pre_pr_cmd` configured does not exit
  `UNCONFIGURED_EXIT` in this mode.

## 3. Wire the gate into orchestrator group integration

Implements requirement: **Every orchestrator group PR clears the drift gate before it exists**.

- [ ] 3.1 Add `QUARANTINE_PRE_PR_DRIFT = "pre_pr_drift"` alongside the existing constants
  at `src/worktrail/orchestrator/integrate.py:46-50`, with a comment distinguishing it
  from `QUARANTINE_INTEGRATION_ERROR` (spec/task bookkeeping drift vs. a failing merged
  tree — different remediation).
- [ ] 3.2 [cleanup] Grep the repo for each existing quarantine reason string
  (`budget_exhausted`, `task_failure`, `merge_conflict`, `integration_error`,
  `dependency_quarantined`) to confirm no consumer enumerates the full set, and that the
  new code correctly falls through to the "real failure, needs human review" default in
  `router/quarantine_selfcheck.py` and `drain/drain.py`. Record the finding; add handling
  only if the grep contradicts design.md's Risks assessment.
- [ ] 3.3 Add `_run_drift_gate(iw: Path, name: str) -> tuple[bool, str]` modeled on
  `_run_integration_smoke` (`integrate.py:637`): resolve the script via
  `_resolve_pre_pr_gate()`, run `[sys.executable, str(gate_script), "--repo", str(iw),
  "--checks-only"]` with `capture_output=True, text=True` and a timeout, print a
  `DRIFT [name]` progress line, and fail closed — an unresolvable script,
  `TimeoutExpired`, `OSError`, or any non-zero exit returns `(False, <short stderr tail>)`.
- [ ] 3.4 Call it in `integrate_one` immediately after
  `_write_group_task_status(iw, spec_id, g, status)` (`integrate.py:1028`) and before the
  `if smoke_cmd:` block. On failure set
  `quarantined[name] = f"pre-PR drift gate failed: {detail}"`, print the `SKIP [name]`
  line, call `_do_journal(name, "", gb, "QUARANTINED", QUARANTINE_PRE_PR_DRIFT)`, and
  `return None` — matching the shape of the smoke-failure branch at `:1030-1035` exactly.

## 4. Tests for the orchestrator wiring

- [ ] 4.1 Regression test for requirement:
  **The gate inspects the group's integrated tree, not the canonical checkout**
  (design D5, the highest-value test in this change). Assert the gate subprocess is
  invoked with `--repo` equal to the group's **integration worktree** path, not the
  canonical repo path. Assert on the recorded argv, not on the gate's outcome, so the
  test cannot pass vacuously.
- [ ] 4.2 Test for requirement:
  **A drift failure quarantines the group instead of opening a PR**.
  A non-zero gate exit quarantines the group — `quarantined[name]` is
  set, the journal records `QUARANTINED` with `quarantine_reason == "pre_pr_drift"`, no
  branch is pushed, no PR is created, and `integrate_one` returns `None`.
- [ ] 4.3 Test that a drift failure short-circuits **before** the smoke command runs
  (assert the smoke command was never invoked when a `smoke_cmd` is configured).
- [ ] 4.4 Happy-path regression: a clean group with a passing gate still runs its smoke
  command, pushes, and opens its PR with unchanged labels.
- [ ] 4.5 Fail-closed test: an unresolvable gate script (or a timeout) quarantines the
  group rather than allowing it through.
- [ ] 4.6 Use the existing `PRE_PR_GATE_SCRIPT` env override to substitute a stub gate in
  these tests rather than adding a new injection seam.

## 5. Reconcile the auto-dod-verification assertion

- [x] 5.1 In `openspec/changes/auto-dod-verification/proposal.md`, annotate the claims at
  lines 31-34 ("the existing diff-scoped `pre_pr_gate.py` wiring … covers derived checks
  with no changes to `pre_pr_gate.py` itself"), line 57 ("opt-out-free"), and lines 74-76
  (`pre_pr_gate.py`: "no functional change") to state that the claim held for one-off
  `/go` routes only, and that orchestrator group PRs were not covered until
  `orchestrator-group-pr-drift-gate` landed. Annotate rather than rewrite — that change is
  20/20 complete and unarchived, so its record of what was true at the time is preserved.

## 6. Verify and ship

- [ ] 6.1 [e2e] Run the full suite: `PYTHONPATH=src pytest -q`.
- [ ] 6.2 [e2e] Run the golden record/replay regression:
  `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`.
- [ ] 6.3 [cleanup] Apply the `go:no-version-bump` label to this PR rather than bumping the version
  inline. `CI: Version Bump Check` fires because this PR touches `src/worktrail/**`, but
  `AGENTS.md` (Versioning) requires the bump to be a standalone
  `chore: bump Worktrail to X.Y.Z` commit touching `pyproject.toml` and
  `.codex-plugin/plugin.json` together — explicitly "not bundled into a feature/fix PR" —
  and records this repo's actual practice as periodic multi-PR batch bumps. Bundling the
  bump here would contradict that contract; the label is the documented mechanism for a
  deliberately deferred batch bump, not a way to silence the check.
- [ ] 6.4 [cleanup] Run `openspec validate orchestrator-group-pr-drift-gate --strict` and
  confirm it passes.
