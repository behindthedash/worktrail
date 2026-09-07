## 1. fold-into-change requires and re-verifies a grounded target_quote (`Evidence-required verdict per brief`)

- [x] 1.1 Implement requirement: In `src/worktrail/workqueue/queue_triage.py`:
      - Add `target_quote: str | None = None` to `Verdict` (beside `target_change`), with a
        docstring note mirroring `refuted_span`'s.
      - Add module-level `_MIN_TARGET_QUOTE_LEN = 12` beside `_MIN_REFUTED_SPAN_LEN`, with a
        docstring noting it mirrors that floor and why.
      - Extend `_has_valid_target()`'s `fold-into-change` branch: require `target_change in
        presented_candidates` (unchanged) AND `target_quote` present as a string of at least
        `_MIN_TARGET_QUOTE_LEN` characters.
      - Extend `parse_verdicts()` to read `target_quote` from the evaluator's JSON object and
        set it on the constructed `Verdict` (string-typed or `None`, matching how
        `target_change` is already handled).
      - Update `EVALUATOR_PROMPT_TEMPLATE`'s Step 2a to require `fold-into-change` to include
        `target_quote`: a verbatim quote of at least 12 characters copied from the candidate's
        own `proposal.md`/`tasks.md` content the evaluator must open and read (not inferred
        from the candidate summary/score already shown, and not restated from the brief's own
        focus text), and add `target_quote` to the per-brief JSON output shape listed later in
        the prompt.
      - In `_apply_fold_into_change()`'s `prepare()` callback, after reading `proposal_text`/
        `tasks_text` and before writing the fold edits, check that `v.target_quote` (re-checked
        for the `_MIN_TARGET_QUOTE_LEN` floor) appears verbatim in `proposal_text` or
        `tasks_text`; if not, return an error string (same shape as the existing "target change
        has no proposal.md/tasks.md" check) instead of writing the edits.

      Add tests in `tests/workqueue/test_queue_triage.py` covering the above:
      - `_has_valid_target("fold-into-change", ...)` returns `False` when `target_quote` is
        missing, empty, or shorter than 12 characters, even when `target_change` is a presented
        candidate; returns `True` when both are satisfied.
      - `parse_verdicts()` on a well-formed `fold-into-change` object populates
        `Verdict.target_quote`; on one with a missing/short `target_quote`, the brief falls back
        to `keep` with the raw verdict retained as evidence (mirrors the existing
        not-a-candidate test).
      - `_apply_fold_into_change()` (or its `prepare()` callback directly) edits the target's
        `proposal.md`/`tasks.md` when `target_quote` is found verbatim in one of them, and fails
        closed (error action-log entry, no file edits, no commit/push/PR) when it is not found
        in either — using a fixture target change in a temp git repo, matching this file's
        existing `_apply_fold_into_change` fixtures.
      - The formatted `EVALUATOR_PROMPT_TEMPLATE` Step 2a text and JSON output shape mention
        `target_quote` and its 12-character minimum.

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm it is green, including the new
      tests from section 1. Verification-only — no file changes expected.
- [ ] 2.2 [e2e] Run `openspec validate target-quote-validation-for-fold-into-change
      --strict` and confirm it passes. Verification-only — no file changes expected.
