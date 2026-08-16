## 1. Stamp structured findings on checkbox-drift briefs

- [x] 1.1 In `src/worktrail/router/spec_sync_sweep_checkbox_brief.py`, extend `_render()` to emit
      a `drift-findings` frontmatter list (one entry per hit: `path`, `unchecked_count`,
      `total_count`) alongside the existing `drift-source: checkbox-drift-sweep` marker, without
      changing the existing prose `## Focus` bullet rendering. Implements Requirement:
      Deterministic Staleness Predicate Is Captured On Sweep-Generated Briefs.
- [x] 1.2 Update `tests/router/test_spec_sync_sweep_checkbox_brief.py` to assert the new
      `drift-findings` frontmatter is present, correctly shaped, and round-trips through
      `brief_frontmatter.read_frontmatter`/`validate_brief` for a multi-hit brief.

## 2. Predicate re-check module

- [x] 2.1 Create `src/worktrail/router/check_brief_predicate.py` with the `recheck(repo,
      frontmatter)` entrypoint (never raises; returns `{"attempted", "drift_source", "outcome",
      "still_true", "resolved", "error"}` per design.md) and the `PREDICATE_RECHECKS` registry
      dict.
- [x] 2.2 Implement `_recheck_checkbox_drift(repo, findings)`: for each `drift-findings` entry,
      read the task file at its `path` via `taskformats.devkit.checkbox_audit`'s existing
      `read_task_file`/`_all_checkboxes_checked`, classify as still-true (still `status:
      completed` with an unchecked box in `COMPLETION_AUDIT_SECTIONS`) or resolved (fully
      checked, or no longer `status: completed`); a finding that cannot be read raises, causing
      the whole brief's recheck to become `outcome="error"`. Implements Requirement:
      Checkbox-Drift Predicate Re-Check Reflects Current Task-File State.
- [x] 2.3 Implement `recheck()`'s outcome selection: `"no-predicate"` when `drift-source` is
      absent, `"unrecognized"` when present but not in `PREDICATE_RECHECKS`, `"error"` when the
      registered function raises or `drift-findings` is missing/empty, `"still-true"` when any
      finding is still-true, `"resolved"` when every finding resolves. Implements Requirement:
      Deterministic Predicate Re-Check Precedes Evidence Surfacing.
- [x] 2.4 Add `tests/router/test_check_brief_predicate.py` covering: no `drift-source`;
      unrecognized `drift-source`; checkbox-drift brief with all findings still drifted
      (still-true); all findings resolved (resolved); mixed still-true/resolved (still-true,
      per design.md's "any still-true wins" rule); a finding whose path no longer exists
      (error); a finding whose file exists but is unreadable/unparseable (error); empty
      `drift-findings` list (error, not resolved).

## 3. CLI entrypoint

- [x] 3.1 Add a `main(argv=None)` CLI to `check_brief_predicate.py` (`--repo`, `--brief`,
      `--json`) that reads the brief's frontmatter via `brief_frontmatter.read_frontmatter` and
      calls `recheck()`, printing the JSON result; register it as `worktrail-recheck-brief-
      predicate` in `pyproject.toml`'s `[project.scripts]`.
- [x] 3.2 Add a CLI-level test (or extend `test_check_brief_predicate.py`) exercising the
      `--repo`/`--brief`/`--json` invocation end to end against a fixture checkbox-drift brief
      and fixture task files.

## 4. Run-record and queue-mutation wiring for the two terminal outcomes

- [x] 4.1 Implements Requirement: Predicate Still True Proceeds Automatically With Recorded Evidence.
      Document (in `check_brief_predicate.py`'s module docstring or a small helper) the
      exact evidence-line format for the `"still-true"` outcome, built from the `still_true`
      list, matching the existing `worktrail-run-record append "$RUN" decisions "..."` pattern
      used for the probe-based "proceed" outcome.
- [x] 4.2 Document the exact closure-note format for the `"resolved"` outcome, built from the
      `resolved` list and naming the predicate re-check explicitly (never a commit SHA or PR
      number), matching the existing `worktrail-work-queue done ... --note "..."` pattern used
      for the probe-based "close as already-delivered" outcome. Implements Requirement:
      Predicate Resolved Closes The Brief Automatically Citing The Re-Check.

## 5. Phase 5.5 skill-doc integration

- [x] 5.1 In `skills/worktrail-go/references/brief-staleness-check.md`, add a new section
      ("Predicate re-check") between the gate paragraph and "Running it", documenting: read the
      claimed brief's frontmatter, run `worktrail-recheck-brief-predicate --repo ... --brief ...
      --json`, and branch on `attempted`/`outcome` before ever reaching today's "Running it"
      step.
- [x] 5.2 Document the `"still-true"` branch: append the run-record evidence line (task 4.1's
      format) once Phase 6 opens the run record, then continue to Phase 6/7 unchanged — no
      operator prompt, no early run-record open (mirrors the existing probe-based "proceed"
      branch's timing).
- [x] 5.3 Document the `"resolved"` branch: call `worktrail-work-queue done` with the closure
      note (task 4.2's format) before Phase 6, report the closure, and stop — no run record is
      opened, matching the existing probe-based "close as already-delivered" branch's timing.
- [x] 5.4 Document that `attempted: false` (outcome `"no-predicate"`, `"unrecognized"`, or
      `"error"`) falls straight through to the existing "Running it" section unchanged, with no
      new behavior inserted between the gate and today's `worktrail-check-brief-staleness`
      invocation.
- [x] 5.5 Update the module's cost/bounds note (if any new subprocess cost is material — reading
      a small, capped number of task files) to keep the "cheap enough that nobody weighs whether
      to run it" property explicit, consistent with the sibling probe-based check.

## 6. Verification

- [x] 6.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm all new and existing tests pass.
- [x] 6.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` (golden
      record/replay regression) and confirm it stays green.
- [x] 6.3 [e2e] Confirm `tests/test_plugin_surface.py` still passes after adding the new console
      script (no plugin/skill directory changes are needed since no new skill is introduced,
      only a new reference-doc section in the existing `worktrail-go` skill).
- [x] 6.4 [e2e] Manually replay the motivating case's shape: construct a fixture checkbox-drift
      brief with `drift-findings` naming two still-`status: completed`/still-unchecked task
      files, run `worktrail-recheck-brief-predicate`, and confirm it reports `outcome:
      "still-true"` (i.e. would have proceeded automatically instead of filing decision
      `20260814-030507-does-merged-pr-46-fix`).
