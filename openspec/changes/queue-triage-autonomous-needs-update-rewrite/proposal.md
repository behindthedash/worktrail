## Why

A `needs-update` verdict fires when a brief's own focus text contains a claim
the evaluator's evidence refutes (a stale target reference, a bug claim
already fixed). Today `_apply_needs_update()` in
`src/worktrail/workqueue/queue_triage.py` only appends an in-place `## Triage
<date>` note carrying that evidence and leaves the brief exactly as it was in
`queue/` — a dead end that requires a human to read the note, hand-rewrite
the brief's `focus:` field to drop the refuted claim, and manually re-trigger
evaluation before the brief can progress. This happened live: brief
`20260903-145001-worktree-missing-committed-openspec-change` needed exactly
that manual rewrite (see its `## Triage 2026-09-03` and `## Triage 2026-09-03
(retriage after rewrite)` notes) before `evaluate-brief-triage` could return
anything but `needs-update` again. `needs-update` is the only verdict type in
this pipeline that never advances a brief and never asks a human either — it
just waits, indefinitely, for someone to notice the note.

## What Changes

- The evaluator prompt's `needs-update` guidance is extended to require the
  evaluator to classify *why* the brief can't proceed as-is, using one of two
  new, mutually exclusive optional `Verdict` fields:
  - `refuted_span`: the exact verbatim substring of the brief's *current*
    `focus:` text that the evidence refutes, plus an optional
    `corrected_span` (empty for pure removal, non-empty to replace the span
    with corrected text) — set only when the fix is mechanical: a specific,
    quotable claim is refuted by cited code/paths, or a specific target
    reference is stale/archived.
  - `judgment_reason`: set instead when resolving the brief requires a
    genuine human call (ambiguous which of several claims is authoritative,
    conflicting requirements, a scope/policy decision) rather than a
    quotable correction.
- `apply`'s `needs-update` handling (`_apply_needs_update()`) branches on
  which field (if either) is present, each re-verified at apply time rather
  than trusted from evaluation time:
  - **Mechanical** (`refuted_span` verified as a verbatim substring of the
    brief's live focus text): the brief's `focus:` is rewritten in place to
    remove/replace exactly that span, an in-place note records the rewrite,
    and evaluation is immediately re-run against the corrected brief —
    producing a fresh verdict with no human hand-edit required. The apply
    action stops there: the fresh verdict is returned in the action-log
    entry for the caller to review and, if desired, apply in a normal
    follow-up `apply --confirm` call — it is never auto-applied itself, so a
    mechanical rewrite can never cascade into closing a brief, folding it, or
    opening a pull request without its own explicit confirm.
  - **Judgment** (`judgment_reason` present, or a `refuted_span` that no
    longer matches the brief's live text): a `worktrail-decision` is filed
    (reusing the existing `needs-decision` apply path/`decisions.ask()`) and
    the brief stays queued, blocked pending a human answer — exactly like
    any other `needs-decision` verdict already behaves.
  - **Neither field present** (today's shape, or a malformed one): behavior
    is unchanged — the note-only dead end this change is meant to eliminate,
    kept as the fail-closed default for a verdict the harness cannot
    mechanically classify.
- Evidence-required-verdict-per-brief and Apply-step-never-closes-a-brief-
  without-an-approved-verdict (both in the `queue-triage` spec) are updated:
  the first to describe the two new optional `needs-update` fields and their
  verbatim-substring validation; the second to describe the narrow addition —
  `apply` may now rewrite a brief's focus text and produce (but not act on) a
  follow-up verdict as part of an already-approved `needs-update` verdict's
  own confirmed action, and may file a decision through the same path
  `needs-decision` already uses.

## Capabilities

### Modified Capabilities
- `queue-triage`: `needs-update` verdicts gain a mechanical-vs-judgment
  classification that either auto-corrects the brief and re-evaluates it, or
  files a human decision, instead of dead-ending in a note only a human will
  notice.

## Impact

- `src/worktrail/workqueue/queue_triage.py`: `EVALUATOR_PROMPT_TEMPLATE`,
  `Verdict` (two new optional fields), `_apply_needs_update()` and its
  helpers, `apply_verdicts()`'s threading of `agent`/`repos_root` into the
  `needs-update` branch.
- `tests/workqueue/test_queue_triage.py`: new coverage for the mechanical
  rewrite + re-evaluation path, the judgment/decision-filing path, and the
  unchanged fallback path.
- No change to `src/worktrail/router/skill_dispatch.py` — `--apply-brief-
  triage` already prints whatever action-log dict `apply_single_brief_verdict()`
  returns, so the enriched entry surfaces automatically.
