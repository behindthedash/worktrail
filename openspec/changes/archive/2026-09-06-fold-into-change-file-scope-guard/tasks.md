## 1. fold-into-change requires evidence naming a file

- [x] 1.1 Implement the requirement: In `src/worktrail/workqueue/queue_triage.py`:
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
