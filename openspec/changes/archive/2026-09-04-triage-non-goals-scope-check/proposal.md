## Why

`rank_change_candidates()` (`src/worktrail/workqueue/queue_triage.py`) scores a repo's
active OpenSpec changes as `fold-into-change` targets purely by lexical overlap between a
brief's focus text and each change's `proposal.md` summary plus `tasks.md` task lines — it
never reads a candidate's own declared Non-Goals / Out-of-scope text
(`grep -n 'Non-Goals|non_goals' src/worktrail/workqueue/queue_triage.py` returns nothing). A
change can therefore score highly and be offered as a fold target for a brief its own author
already excluded: `shared-pr-landing-pipeline`'s proposal is scoped to PR-landing mechanics
(compile marker, labels, CI watch) and explicitly disclaims changing unrelated orchestration,
yet shares enough vocabulary with an evaluator-logic brief (both proposals mention
`queue_triage.py`) to clear the existing `_MIN_CANDIDATE_SCORE` floor on summary/task overlap
alone. When a bad fold like this reaches `compile`, the failure is hard to diagnose after the
fact: `_print_scope_gap_error()` (`compile.py:790-797`) reports only the task IDs left without
file scope, never the conflicting change or the excluded topic that caused the mismatch — so
the wrong-fold root cause is invisible by the time anyone is debugging a scope-gap error.

## What Changes

- `rank_change_candidates()` additionally reads each candidate's Non-Goals / Out-of-scope
  text (from `proposal.md` and, when present, `design.md`) and excludes a candidate that
  otherwise clears the summary/tasks floor when that text overlaps the brief's focus tokens
  at or above the same `_MIN_CANDIDATE_SCORE` coefficient. A candidate's own declared
  exclusion of a topic overrides positive lexical overlap elsewhere in its proposal.
- A change with no Non-Goals / Out-of-scope marker in either file is unaffected — this is a
  pure additional filter, not a change to how eligible candidates are scored or ranked.
- Because the evaluator (`EVALUATOR_PROMPT_TEMPLATE`) only ever sees the candidate list this
  function returns, an excluded candidate is never presented as a `fold-into-change` option
  in the first place — the evaluator cannot pick a target it never sees.

## Capabilities

### Modified Capabilities

- `queue-triage`: fold-candidate ranking (`rank_change_candidates()`) now excludes a
  candidate change whose own declared Non-Goals / Out-of-scope text overlaps the brief's
  focus, in addition to the existing minimum lexical-overlap floor.

## Impact

- **Code**: `src/worktrail/workqueue/queue_triage.py` — new `_NON_GOAL_SECTION_RE`,
  `_non_goal_tokens()`, and an added exclusion check inside `rank_change_candidates()`.
- **Tests**: `tests/workqueue/test_queue_triage.py` — new coverage for the Non-Goals
  exclusion (in `proposal.md` and in `design.md`), for an unrelated Non-Goals section not
  excluding a candidate, and for `_non_goal_tokens()` itself.
- **Non-goals**: changing `_MIN_CANDIDATE_SCORE`, `_overlap_coefficient`/`_tokenize`
  themselves, or how eligible candidates are ranked/truncated to `top_k`; changing
  `_print_scope_gap_error()` or any other compile-time diagnostic (the masking effect cited
  above is evidence of impact, not a target of this change — compile.py already reports
  exactly what it is designed to report, a set of task IDs without file scope, and giving it
  fold-attribution context is a separate concern from preventing the bad fold at the source);
  changing the evaluator prompt text itself (Step 2a already restricts `fold-into-change` to
  the presented candidate list, which this change makes more accurate by construction).
