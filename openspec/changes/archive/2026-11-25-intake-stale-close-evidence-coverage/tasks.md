## 1. Require per-failure coverage for stale-close

- [x] 1.1 Add deterministic focus-item extraction and enforce stale-close coverage. In
      `src/worktrail/workqueue/premise_check.py`, add `reported_failure_items()`: split a brief's
      focus at sentence-ending punctuation followed by whitespace, semicolons, commas followed by
      whitespace, list-item line breaks, and the stand-alone word `and`; whitespace-collapse and
      strip surrounding punctuation from each item; de-duplicate; drop items shorter than 12
      characters. In `src/worktrail/workqueue/queue_triage.py`, add `failure_coverage` to
      `Verdict` and copy/sanitize well-formed `{failure, evidence}` entries through
      `parse_verdicts()`; state the coverage rule in `EVALUATOR_PROMPT_TEMPLATE` (one verbatim
      focus-item span plus resolving evidence per independently reported failure, and an
      uncovered item means the verdict is downgraded to keep) and list `failure_coverage` in its
      per-brief JSON shape; synthesize one coverage entry per focus item in the archived-repo
      short-circuit; and enforce coverage in both `_apply_close()` and `_preview_verdict()` by
      re-deriving items from the brief's live focus, returning a downgraded-to-keep no-op that
      leaves the brief untouched and names the uncovered items. Add tests in
      `tests/workqueue/test_premise_check.py` for sentence/conjunction/comma/semicolon/list
      splits, de-duplication, short-item exclusion, and the incident's two-item focus; add tests
      in `tests/workqueue/test_queue_triage.py` for parse retention and sanitization, the prompt
      rule and JSON shape, archived synthesis, a fully covered multi-failure close, a
      single-repair downgrade that leaves the brief in `queue/`, preview/apply agreement, and a
      span crossing two items covering neither. Update existing stale-close fixtures in that file
      whose focuses now yield counted items.
      (Requirement: Stale-close verdicts account for every reported failure)
      files: src/worktrail/workqueue/premise_check.py src/worktrail/workqueue/queue_triage.py tests/workqueue/test_premise_check.py tests/workqueue/test_queue_triage.py
- [x] 1.2 Update the remaining stale-close fixtures for the new coverage rule. In the workqueue
      and router triage suites that construct `stale-close` verdicts against briefs whose focus
      yields one or more counted items, add `failure_coverage` entries (or deliberately shorten
      a test focus) so each test keeps asserting its original behavior instead of accidentally
      exercising the new downgrade: `tests/workqueue/test_queue_triage_inventory.py`,
      `tests/workqueue/test_queue_triage_live_replay.py`,
      `tests/workqueue/test_triage_evaluator_exhaustion.py`, and
      `tests/router/test_skill_dispatch.py`.
      (Requirement: Stale-close verdicts account for every reported failure)
      files: tests/workqueue/test_queue_triage_inventory.py tests/workqueue/test_queue_triage_live_replay.py tests/workqueue/test_triage_evaluator_exhaustion.py tests/router/test_skill_dispatch.py

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/workqueue/test_premise_check.py tests/workqueue/test_queue_triage.py
      tests/workqueue/test_queue_triage_inventory.py
      tests/workqueue/test_queue_triage_live_replay.py
      tests/workqueue/test_triage_evaluator_exhaustion.py
      tests/router/test_skill_dispatch.py`, then `PYTHONPATH=src python3.14 -m pytest -q` and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`. Run
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .`, and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Run
      `openspec validate intake-stale-close-evidence-coverage --strict` and
      `worktrail-compile openspec/changes/intake-stale-close-evidence-coverage`. Confirm a
      stale-close whose coverage names one of the two incident failures is preserved as keep
      while full two-item coverage still closes. No file changes are expected.
      (Requirement: Stale-close verdicts account for every reported failure)
      depends: 1.1, 1.2
