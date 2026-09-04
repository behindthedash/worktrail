## 1. fold-into-change requires and re-verifies a grounded target_quote

- [ ] 1.1 Implement requirement: In `src/worktrail/workqueue/queue_triage.py`:
      - Add `target_quote: str | None = None` to `Verdict` (beside `target_change`), with a
        docstring note mirroring `refuted_span`'s.
      - Update `EVALUATOR_PROMPT_TEMPLATE`'s Step 2a to require `fold-into-change` to include
        `target_quote`: a verbatim quote of at least 12 characters, copied from the candidate's
        own `proposal.md`/`tasks.md` content (which the evaluator must open and read, not infer
        from the summary/score already shown), demonstrating what in that change this brief
        folds into — a quote restated from the brief's own focus text does not satisfy this.
        Add `target_quote` to the per-brief JSON output shape listed later in the prompt.
      - Add module-level `_MIN_TARGET_QUOTE_LEN = 12` beside `_MIN_REFUTED_SPAN_LEN`, with a
        docstring noting it mirrors that floor.
      - Extend `_has_valid_target()`'s `fold-into-change` branch: require `target_change in
        presented_candidates` (unchanged) AND `target_quote` present as a string of at least
        `_MIN_TARGET_QUOTE_LEN` characters.
      - Extend `parse_verdicts()` to read `target_quote` from the evaluator's JSON object and
        set it on the constructed `Verdict` (string-typed or `None`, matching how
        `target_change` is already handled).
      - In `_apply_fold_into_change()`'s `prepare()` callback, after reading `proposal_text`/
        `tasks_text` and before writing the fold edits, check that `v.target_quote` (re-checked
        for the `_MIN_TARGET_QUOTE_LEN` floor) appears verbatim in `proposal_text` or
        `tasks_text`; if not, return an error string (same shape/pattern as the existing
        "target change has no proposal.md/tasks.md" check) instead of writing the edits.

      Add tests in `tests/workqueue/test_queue_triage.py`:
      - `_has_valid_target("fold-into-change", ...)` returns `False` when `target_quote` is
        missing, empty, or shorter than 12 characters, even when `target_change` is a presented
        candidate.
      - `_has_valid_target("fold-into-change", ...)` returns `True` when both `target_change`
        is presented and `target_quote` is at least 12 characters.
      - `parse_verdicts()` on a well-formed `fold-into-change` JSON object populates
        `Verdict.target_quote`; on one missing/short `target_quote`, the brief falls back to
        `keep` with the raw verdict retained as evidence (mirrors the existing
        not-a-candidate test).
      - `_apply_fold_into_change()` (or its `prepare()` callback directly) succeeds and edits
        the target's `proposal.md`/`tasks.md` when `target_quote` is found verbatim in one of
        them, and fails closed (error action-log entry, no file edits, no worktree
        commit/push) when `target_quote` is not found in either — using a fixture target change
        checked out in a temp git repo, matching this file's existing `_apply_fold_into_change`
        test fixtures.
      - `EVALUATOR_PROMPT_TEMPLATE`'s formatted Step 2a text mentions `target_quote` and its
        minimum length.

## 2. Verification

- [ ] 2.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm it is green, including the new
      tests from 1.2. Verification-only — no file changes expected.
- [ ] 2.2 [cleanup] Run `openspec validate evaluate-brief-triage-determinism-fix --strict` and
      confirm it passes. Verification-only — no file changes expected.
