## Why

`brief-staleness-check.md`'s probe-based flow treats any non-empty `matches`/`pull_requests`
as a two-way branch straight to the operator prompt (interactively) or a filed decision record
plus queue release (`AUTO_MODE`) — there is no step that checks whether the brief's actual
requested change is present in current file state before asking. Live incident (2026-08-17,
brief `20260817-102329`): the guard surfaced merged PR #500 touching `ci-watch-loop.md`, the
dispatching agent read the file and confirmed the brief's specific requested capability (a
REST/`gh api` fallback for `--watch`/`--json`) was still absent — PR #500 delivered a different
fix (stuck-check-run status-lag fallback) — yet the guard still fired the operator prompt. The
interrupt carried zero decision value once the absence was already established. In `AUTO_MODE`
the same false positive costs a full decision-record-plus-release round trip, not just one
prompt.

The `stale-brief-precheck` spec already has precedent for this exact shape of fix: the
deterministic predicate re-check (`check_brief_predicate.py`) auto-resolves a brief when its
own structured `drift-findings` are re-verified, bypassing the operator prompt entirely. This
change extends that same auto-resolve contract to the probe-based branch, where the requested
change is prose-described rather than structurally captured, so full determinism is not
possible — the resolution is a bounded, instructed agent read/grep step, not a new deterministic
Python check.

## What Changes

- Insert a **file-state verification step** in `brief-staleness-check.md`, between "Running it"
  (probe matches computed) and "The operator prompt", that runs whenever `matches` or
  `pull_requests` is non-empty and the predicate re-check did not already decide the outcome.
  The dispatching agent reads/greps the brief's named paths and symbols for the *specific
  capability* its focus prose describes, and classifies the result as one of three outcomes:
  - **Verifiably absent** — proceed automatically, no prompt; append an evidence line to the
    run record after Phase 6 citing both the probe matches and the verification finding.
  - **Verifiably present** — close the brief automatically as already-delivered, citing both
    the probe matches and the verification finding, before Phase 6 opens a run record.
  - **Inconclusive** — fall through to today's unmodified operator prompt (interactive) or
    decision-record-plus-release (`AUTO_MODE`) flow, unchanged.
- Add two formatting helpers to `check_brief_staleness.py`, mirroring
  `check_brief_predicate.py`'s `format_still_true_evidence`/`format_resolved_closure_note`, so
  the verified-absent evidence line and verified-present closure note are built the same
  canonical way regardless of which auto-resolve path produced them.
- Extend the `stale-brief-precheck` spec's `Evidence Is Surfaced To The Operator, Never
  Auto-Applied` requirement with a second carve-out (the file-state verification outcome),
  parallel to the existing predicate-re-check carve-out, plus two new requirements describing
  the verified-absent and verified-present auto-resolve outcomes — mirroring the structure of
  `Predicate Still True Proceeds Automatically With Recorded Evidence` and `Predicate Resolved
  Closes The Brief Automatically Citing The Re-Check`.
- This applies identically to the interactive `AskUserQuestion` path and the `AUTO_MODE`
  decision-record-plus-release path: the verification step is an internal agent reasoning step
  (not a human-facing prompt), so it runs the same way in both modes, and only the remaining
  `INCONCLUSIVE` case reaches the mode-specific prompt/decision-record behavior.
- Out of scope: the search-boundary timestamp precedence (`released-at:`/`original-created:`)
  is being changed by two other in-flight changes
  (`stale-brief-precheck-recheck-search-boundary`,
  `stale-brief-precheck-consolidation-original-created`) against the `History Search Is Bounded
  By The Brief's Capture Time` requirement — this change does not touch that requirement.

## Capabilities

### Modified Capabilities
- `stale-brief-precheck`: `Evidence Is Surfaced To The Operator, Never Auto-Applied` gains a
  second, file-state-verification-based carve-out alongside the existing predicate-re-check
  carve-out; two new requirements describe the verified-absent and verified-present auto-resolve
  outcomes, mirroring the existing predicate-re-check requirement pair.

## Impact

- `skills/worktrail-go/references/brief-staleness-check.md`: new verification step between
  "Running it" and "The operator prompt"; the "Reading the result" table's non-empty-evidence
  row now points to the verification step instead of straight to the prompt; the `AUTO_MODE`
  section's decision-record-plus-release path now only fires on the `INCONCLUSIVE` outcome.
- `src/worktrail/router/check_brief_staleness.py`: two new formatting helper functions for the
  verified-absent evidence line and verified-present closure note.
- `openspec/specs/stale-brief-precheck/spec.md`: one requirement's text (new carve-out) plus two
  new requirements and their scenarios.
- `tests/router/test_check_brief_staleness.py`: coverage for the two new formatting helpers.
- No CLI flags, public function signatures beyond the two additions, or storage layout changes.
