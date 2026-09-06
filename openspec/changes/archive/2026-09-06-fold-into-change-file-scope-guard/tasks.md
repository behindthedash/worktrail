## 1. fold-into-change requires evidence naming a file

- [ ] 1.1 NOT IMPLEMENTED — archived without landing. Verified 2026-09-06: no code matching
      this task exists in `src/worktrail/workqueue/queue_triage.py` on `main` (no
      `extract_needles` filter, no file-path check in `_has_valid_target()`'s
      `fold-into-change` branch) and no test in `tests/workqueue/test_queue_triage.py`
      covers it. This change is being archived as superseded, not completed: `main`'s
      `evaluate-brief-triage-determinism-fix` change independently proposed the stronger
      `target_quote` requirement for the same gap (queue-triage spec's "Evidence-required
      verdict per brief"), but that change's own task 1.1 was *also* checked off and
      archived without the code landing. Neither the file-path check nor `target_quote`
      validation exists in `_has_valid_target()` today — see the `worktrail-handoff` filed
      for implementing `target_quote` validation, which supersedes this task's approach.
      Original task text, retained for history:
      Implement the requirement: In `src/worktrail/workqueue/queue_triage.py`:
      - In `_has_valid_target()`'s `fold-into-change` branch, after confirming
        `target_change` is one of `presented_candidates`, additionally require `obj`'s
        `evidence` to be a string containing at least one needle of kind `"path"` per
        `premise_check.extract_needles(evidence)` (filter its returned list to
        `n.kind == "path"`) -- reusing the existing path-probe extraction
        `run_premise_check()` already runs against a brief's `focus:` text, rather than
        adding a second path-detection regex.
      - Update `_has_valid_target()`'s docstring to note the new `evidence` requirement for
        `fold-into-change` and why (the compile gate needs a file reference in the task text
        `_apply_fold_into_change()` appends verbatim).
      - Update `EVALUATOR_PROMPT_TEMPLATE`'s Step 2a to state explicitly that a
        `fold-into-change` verdict's `evidence` must cite at least one specific file path,
        and that evidence naming no file is downgraded to `keep`.

      Add tests in `tests/workqueue/test_queue_triage.py`:
      - `parse_verdicts()` on a well-formed `fold-into-change` JSON object whose `evidence`
        cites a file path (e.g. `src/widgets/export.py`) still parses as `fold-into-change`
        (update the existing `test_well_formed_fold_into_change_verdict_is_parsed_as_is` to
        use such evidence, since plain prose with no file reference no longer qualifies).
      - A new test: `parse_verdicts()` on an otherwise well-formed `fold-into-change` JSON
        object whose `evidence` cites no file path downgrades that brief to `keep`, with the
        raw verdict snippet retained as evidence -- mirroring the existing
        `test_fold_into_change_target_outside_candidate_list_downgrades_to_keep` test's
        shape.
      - `EVALUATOR_PROMPT_TEMPLATE`'s formatted Step 2a text mentions the file-path
        requirement for `fold-into-change` evidence.

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm it is green, including the new
      tests from 1.1. Verification-only — no file changes expected.
- [ ] 2.2 [e2e] Run `openspec validate fold-into-change-file-scope-guard --strict` and
      confirm it passes. Verification-only — no file changes expected.
