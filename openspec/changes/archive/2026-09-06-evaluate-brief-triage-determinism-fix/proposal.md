## Why

`--evaluate-brief-triage` is non-deterministic across identical re-runs and can pick a wrong
`fold-into-change` target. Reported incident: evaluating the same brief against the same repo
twice with identical args returned `work-directly` (correct) on one run and `fold-into-change`
targeting `shared-pr-landing-pipeline` (wrong -- that change's own remaining work does not
match the brief; the brief's own text points elsewhere) on another. Independently confirmed
that `shared-pr-landing-pipeline` was, in fact, offered to the evaluator as a candidate (per
`rank_change_candidates()`, `src/worktrail/workqueue/queue_triage.py:769-830`), since it scores
above `_MIN_CANDIDATE_SCORE` purely on token overlap between the brief's focus text and that
change's `proposal.md`/`tasks.md` vocabulary.

The evaluator prompt's Step 2a (`queue_triage.py:156-167`) already restricts `fold-into-change`
to naming one of the presented candidates, and `_has_valid_target()`
(`queue_triage.py:1339-1362`) mechanically enforces that restriction. Neither requires the
evaluator to demonstrate it actually read and matched the *content* of the candidate it picked
-- a `target_change` that merely cleared the token-overlap score floor is accepted exactly like
one the evaluator verified by inspection. That is the reliability gap the brief describes as
"insufficiently grounded" target selection: on one run the model happens to look closer and
concludes the presented candidate is a poor fit (falling through to `work-directly`); on
another it accepts the same coincidentally-scored candidate without further scrutiny. Nothing
in the pipeline currently forces the second behavior to fail.

`needs-update`'s `refuted_span` field (spec `queue-triage`, "Evidence-required verdict per
brief") already solves an analogous problem for a different verdict type: it forces the
evaluator to commit to a verbatim, checkable quote rather than free-form judgment, and
`_needs_update_is_mechanical()` (`queue_triage.py:1701-1714`) re-verifies that quote against
live state at apply time before acting on it. `fold-into-change` has no equivalent -- its only
apply-time work (`_apply_fold_into_change()`, `queue_triage.py:2608-2681`) trusts `v.evidence`
outright and never re-reads the target change's own content before opening a fold PR.

## What Changes

- **Evaluator prompt and verdict schema**: `fold-into-change` gains a required `target_quote`
  field -- a verbatim quote (minimum length, mirroring `needs-update`'s existing
  `_MIN_REFUTED_SPAN_LEN` floor) copied from the *candidate's own* `proposal.md` or `tasks.md`
  content (which the evaluator must open and read, not infer from the summary/score shown in
  the prompt) that specifically supports folding this brief into it. A quote restated from the
  brief's own focus text does not satisfy this -- it must come from the target change.
- **Parse-time validation**: `_has_valid_target()` rejects a `fold-into-change` whose
  `target_quote` is missing or shorter than the minimum floor, exactly like a `target_change`
  that wasn't a presented candidate -- both fall back to `keep` with the raw verdict retained
  as evidence.
- **Apply-time re-verification**: before `_apply_fold_into_change()` edits the target change's
  `proposal.md`/`tasks.md`, it re-checks that `target_quote` still appears verbatim in that
  change's current on-disk content (the freshly checked-out worktree, not evaluation-time
  state) -- mirroring `_needs_update_is_mechanical()`'s live re-check pattern. A quote that
  doesn't verify (fabricated at evaluation time, or the target change's content has since
  changed) fails the apply closed with an error action-log entry, exactly like the existing
  "target change has no proposal.md/tasks.md" failure -- no fold PR is opened.

This does not make the evaluator itself deterministic (it remains an LLM judgment call), but it
closes the specific gap the incident exposed: a `fold-into-change` can no longer be accepted or
applied on token-overlap ranking alone. Requiring and re-verifying a real quote from the
target's own content is what the brief's item (3) asks for -- "a stronger confirmation signal
... before an unattended `apply-brief-triage --confirm` would land a wrong-target fold" -- using
the same mechanical-quote pattern this spec already trusts for `needs-update`, rather than a new
verification mechanism.

## Capabilities

### Modified Capabilities
- `queue-triage`: `Evidence-required verdict per brief` gains the `target_quote` requirement
  for `fold-into-change`; `Apply step never closes a brief without an approved verdict` gains
  the apply-time re-verification of that quote against the target change's live content.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`EVALUATOR_PROMPT_TEMPLATE`, `Verdict`,
  `_has_valid_target()`, `parse_verdicts()`, `_apply_fold_into_change()`)
- `tests/workqueue/test_queue_triage.py`
