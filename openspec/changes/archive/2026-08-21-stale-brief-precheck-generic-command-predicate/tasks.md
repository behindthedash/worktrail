## 1. Command predicate core

- [x] 1.1 In `src/worktrail/router/check_brief_predicate.py`, add `_recheck_command_predicate(repo,
      findings)` and wire it in: validate each finding carries a `path` and a `predicate-cmd` that
      is a non-empty list of strings (a string, empty list, or non-string element raises), enforce
      the per-brief command cap (20) *before* executing anything, then run each `predicate-cmd`
      with `subprocess.run(argv, shell=False, cwd=repo, capture_output=True, text=True,
      timeout=30)` merging stderr into the captured output. Classify exit `0` as still-true, exit
      `1` as resolved, and raise on any other status, a `TimeoutExpired`, or an `OSError` (missing
      executable) so `recheck()`'s existing `except` degrades the whole brief to
      `outcome="error"`. Return `{"still_true": [...], "resolved": [...], "evidence": [...]}` with
      each evidence entry `{"path", "command" (shlex-joined), "exit", "output" (truncated,
      truncation marked)}`. In `recheck()`, add the dispatch fallback per design.md Decision 1
      (`PREDICATE_RECHECKS.get(drift_source)` first, else `predicate-kind == "command"` ->
      `_recheck_command_predicate`, else `outcome="unrecognized"` unchanged) and pass a recheck
      function's optional `evidence` through to the result dict (defaulting to `[]`) so the
      `--json` contract gains the field additively. Update the module docstring to document the
      `predicate-kind: command` / `predicate-cmd` capture contract, the fixed `0 = predicate still
      holds` polarity, and the execution bounds.
      (Requirement: Command Predicate Re-Check Classifies By Exit Status)
      (Requirement: Command Predicate Execution Is Bounded And Shell-Free)
      (Requirement: Deterministic Predicate Re-Check Precedes Evidence Surfacing)
      (Requirement: Deterministic Staleness Predicate Is Captured On Sweep-Generated Briefs)
- [x] 1.2 In `tests/router/test_check_brief_predicate.py`, cover the core: exit `0` -> still-true,
      exit `1` -> resolved, mixed findings -> still-true, exit `2`/`127`/timeout -> `error`, a
      shell-metacharacter argument passed through literally (assert no shell interpretation), a
      string `predicate-cmd` rejected without being shell-split, a finding missing `predicate-cmd`
      -> `error`, over-cap findings -> `error` with zero executions, `cwd` pinned to `--repo`, and
      dispatch order (a brief with both a registered `drift-source` and `predicate-kind: command`
      runs the registered predicate and executes no command; a `predicate-kind: command` brief
      with an unregistered `drift-source` runs the command predicate; neither present ->
      `unrecognized`). Use real short executables (a generated script or `python3 -c`) rather than
      mocking `subprocess`, per the suite's fake-over-mock convention.

## 2. Evidence transcript

- [x] 2.1 In `src/worktrail/router/check_brief_predicate.py`, extend
      `format_still_true_evidence` and `format_resolved_closure_note` to append a re-run
      transcript section when the result's `evidence` list is non-empty, rendering per finding the
      `command: ...` / `exit: ...` / `output: ...` line shape from design.md Decision 4 (each on
      its own line, newlines in captured output collapsed). Both strings SHALL be byte-for-byte
      unchanged when `evidence` is empty, so the checkbox-drift path is untouched.
      (Requirement: Predicate Still True Proceeds Automatically With Recorded Evidence)
      (Requirement: Predicate Resolved Closes The Brief Automatically Citing The Re-Check)
- [x] 2.2 In `tests/router/test_check_brief_predicate.py`, assert the transcript output: both
      formatters unchanged for an evidence-less (checkbox-drift) result; both include the executed
      command, exit status, and truncated output for a command-predicate result; and — the
      cross-module contract from design.md Decision 4 —
      `work_queue._reverification_claim_missing_evidence(<generated closure note>) is False`, so a
      drift in either module's format fails a test instead of silently rejecting auto-closures at
      dispatch time.

## 3. Phase 5.5 skill doc

- [x] 3.1 Update `skills/worktrail-go/references/brief-staleness-check.md`'s "Predicate re-check"
      section: state the dispatch order (registered `drift-source`, then `predicate-kind:
      command`, then unrecognized), add the capture-side contract an external sweep must satisfy
      (`predicate-kind: command`, per-finding `path` + `predicate-cmd` argv list, `0 = the
      condition still holds`, bounds) with a worked frontmatter example, show the still-true and
      resolved strings including their transcript sections, and correct the cost claim near line
      272 that says this step "spawns no subprocess" — it now may spawn up to the per-brief cap.

## 4. Release and verification

- [x] 4.1 Bump `version` in `pyproject.toml` (`src/worktrail/**` changed; `CI: Version Bump Check`
      requires it absent a `go:no-version-bump` label). Already satisfied: PR #605's own
      `Version Bump Check` ci-fix bumped `pyproject.toml`/`.codex-plugin/plugin.json` to 1.1.26
      (parity confirmed) while landing this change's `src/worktrail/**` edits, so no further bump
      was needed once this task ran in isolation against its own already-merged diff.
- [x] 4.2 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both are green, then run
      `worktrail-recheck-brief-predicate --repo <repo> --brief <fixture> --json` against a
      throwaway `predicate-kind: command` brief in a scratch `$WORK_QUEUE_DIR` and confirm the
      reported `outcome`, `still_true`/`resolved`, and `evidence` match the executed commands'
      actual exit statuses. Verified against `main`@`3b8bd9a`: pytest 4142 passed/2 skipped;
      golden orchestrator check passed; throwaway two-finding fixture (exit 0 / exit 1) returned
      `outcome: still-true`, `still_true: [pyproject.toml]`, `resolved: [README.md]`, matching the
      executed commands' exit statuses exactly.
