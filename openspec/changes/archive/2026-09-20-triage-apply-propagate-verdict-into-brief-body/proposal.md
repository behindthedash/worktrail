## Why

A `work-directly` verdict is the one triage outcome that hands a brief straight to a
worker: apply stamps `recommended-route: F` and leaves the brief in `queue/`, so the next
Route F run reads that brief's body as its task statement.

Confirmed by inspection 2026-09-20 (`grep -rn "seeded-from\|recommended-route" --include=*.py
src/`, `sed -n 2500,2575p src/worktrail/workqueue/queue_triage.py`): `_apply_work_directly()`
(`src/worktrail/workqueue/queue_triage.py:2510`) calls
`_set_fm_fields(path, {"seeded-from": ..., "recommended-route": "F"})` and nothing else. It
never rewrites the brief's `focus`, and -- unlike `_apply_keep()`,
`_apply_propose_change_wip_cap_downgrade()`, and `_apply_needs_update_mechanical()` -- it never
appends the `## Triage <run_date>` note carrying the evidence either. The body a Route F worker
reads is byte-identical to the body the evaluator just read and partly refuted.

Body rewriting exists in exactly one place, the `needs-update` path
(`_apply_needs_update_mechanical()`, built on `refuted_span`/`corrected_span`;
`src/worktrail/workqueue/queue_triage.py:1709` for the fields). The evaluator prompt
(Step 2c) currently instructs those fields for `needs-update` only, so a `work-directly`
verdict has no channel for "this part of the brief is wrong, the rest is actionable" --
the evaluator's choices are to correct the brief (`needs-update`, which does not seed) or
to seed it (`work-directly`, which does not correct). The stale claim then reaches the
worker with the run's own evidence nowhere on the page, and the worker re-derives -- or
worse, acts on -- a claim triage already disproved.

## What Changes

- The evaluator prompt's Step 2b and its output-shape block extend
  `refuted_span`/`corrected_span` to `work-directly`: a verdict may seed a brief *and* carry
  one quotable correction to its focus text, under the same verbatim-quoting rule Step 2c
  already states for `needs-update`. `judgment_reason` stays `needs-update`-only -- a brief
  needing a human call is not directly actionable, and so is not `work-directly`.
- `_apply_work_directly()` propagates the verdict into the brief body before stamping:
  when the verdict carries a span that is still mechanically usable against the brief's live
  focus text (`_needs_update_is_mechanical()`, the same gate the `needs-update` path uses, so
  a stale span is never rewritten blind), it rewrites the focus; then, in every accepted case,
  span or not, it appends a `## Triage <run_date>` note recording the rewrite summary (when
  there was one) and the verdict's evidence.
- A `work-directly` rewrite that would leave no focus text at all is not a correction but a
  refutation of the whole brief, so it does not seed: it downgrades to `keep` with that reason
  as its note, rather than stamping Route F onto an empty brief or filing a decision.
- `_preview_verdict()`'s `work-directly` branch previews the same three outcomes -- planned
  rewrite, planned note, planned downgrade -- so a preview and its `--confirm` run still agree.

## Capabilities

### New Capabilities
- `triage-apply-verdict-propagation`: an applied `work-directly` verdict leaves its correction
  and its evidence in the brief body the Route F worker reads.

### Modified Capabilities

## Impact

- `src/worktrail/workqueue/queue_triage.py` (evaluator prompt, `_apply_work_directly()`,
  `_preview_verdict()`).
- `tests/workqueue/test_queue_triage.py`.
- A seeded brief's body changes: `work-directly` now edits `focus` and appends a note where it
  previously only touched frontmatter. The frontmatter stamp itself
  (`seeded-from`/`recommended-route`) and the `_work_directly_accepted()` downgrade rule are
  unchanged, as are all seven other verdict types.
