## Why

`openspec/specs/queue-triage/spec.md`'s "Evidence-required verdict per brief" requirement
already states that `fold-into-change` "SHALL additionally require a non-empty
`target_change` ... **and a non-empty `target_quote` of at least 12 characters** ... copied
verbatim from the *target change's own* `proposal.md` or `tasks.md`" (spec.md:60-66), with
scenarios pinning both the well-formed case (spec.md:113-119) and the downgrade-to-`keep`
case (spec.md:126-130). "Apply step never closes a brief without an approved verdict"
likewise requires `apply --confirm` to re-check the quote against the target change's
freshly checked-out on-disk content and fail closed when it no longer verifies
(spec.md:263-271, scenarios at spec.md:335-347).

None of it is implemented. `grep -n "target_quote\|_MIN_TARGET_QUOTE_LEN"
src/worktrail/workqueue/queue_triage.py tests/workqueue/test_queue_triage.py` returns zero
hits on `main`. `_has_valid_target()`'s `fold-into-change` branch
(`src/worktrail/workqueue/queue_triage.py:1451-1453`) reads only `obj.get("target_change")`
and checks membership in `presented_candidates`; `Verdict` has no `target_quote` field
(`queue_triage.py:1367` carries `refuted_span` but nothing analogous for folds);
`parse_verdicts()` never reads the key; `EVALUATOR_PROMPT_TEMPLATE`'s Step 2a and its
per-brief JSON output shape (`queue_triage.py:171-181`, `216-227`) never ask for it; and
`_apply_fold_into_change()`'s `prepare()` callback (`queue_triage.py:2815-2843`) reads
`proposal_text`/`tasks_text` and writes the fold edits without verifying any quote against
them.

The requirement text was written and archived by
`openspec/changes/archive/2026-09-06-evaluate-brief-triage-determinism-fix/`, whose
`tasks.md` section 1.1 spells out exactly this implementation — the spec landed, the code
never did. The live consequence: a `fold-into-change` verdict is accepted on the strength of
a candidate id alone, so an evaluator that never opened the target change's files can fold a
brief into it, and `apply --confirm` will edit, commit, push, and open a PR against that
change with no evidence the fold has anything to do with what the change actually says.

No active OpenSpec change touches `_has_valid_target()`, `Verdict`, or
`_apply_fold_into_change()`, so this needs its own change rather than a fold.

## What Changes

- `Verdict` gains a `target_quote: str | None` field, and `parse_verdicts()` reads
  `target_quote` from the evaluator's JSON object the same way it already reads
  `target_change`.
- A module-level `_MIN_TARGET_QUOTE_LEN = 12` is added beside `_MIN_REFUTED_SPAN_LEN`,
  sharing that floor's rationale (a shorter quote matches unrelated text coincidentally).
- `_has_valid_target()`'s `fold-into-change` branch additionally requires `target_quote` to
  be a string of at least `_MIN_TARGET_QUOTE_LEN` characters, so a fold verdict without one
  falls back to `keep` through the existing fail-open path.
- `EVALUATOR_PROMPT_TEMPLATE`'s Step 2a asks for `target_quote` (verbatim, ≥12 characters,
  quoted from the candidate's own `proposal.md`/`tasks.md` that the evaluator opened and
  read), and the per-brief JSON output shape lists it beside `target_change`.
- `_apply_fold_into_change()`'s `prepare()` callback re-checks `v.target_quote` (including
  the length floor) verbatim against the freshly checked-out `proposal_text`/`tasks_text`
  before writing any fold edit, returning an error string in the same shape as the existing
  "target change has no proposal.md/tasks.md" check when it is not found — so the fold fails
  closed with no edit, commit, push, or PR.

This change adds no new spec behavior beyond one delta clause: that the evaluator prompt must
itself ask for `target_quote`. Everything else here is implementing requirement text that is
already in `openspec/specs/queue-triage/spec.md`.

## Capabilities

### Modified Capabilities
- `queue-triage`: `Evidence-required verdict per brief` gains a requirement that the
  evaluator prompt's fold guidance and per-brief JSON output shape ask for `target_quote`,
  since a required field the prompt never requests would downgrade every `fold-into-change`
  verdict to `keep`.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`EVALUATOR_PROMPT_TEMPLATE`, `Verdict`,
  `_MIN_TARGET_QUOTE_LEN`, `_has_valid_target()`, `parse_verdicts()`,
  `_apply_fold_into_change()`)
- `tests/workqueue/test_queue_triage.py`
