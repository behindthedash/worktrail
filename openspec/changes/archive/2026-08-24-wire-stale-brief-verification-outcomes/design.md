## Context

See proposal.md - Why. `skills/worktrail-go/references/brief-staleness-check.md` already has a
"File-state verification" section (added by the archived
`2026-08-17-stale-brief-precheck-verify-before-prompt` change, tasks 2.1/2.2) that classifies
probe-based evidence into `verifiably-absent` / `verifiably-present` / `inconclusive`, but three
downstream sections still treat all non-empty evidence identically:

- "The operator prompt" fires on any non-empty `matches`/`pull_requests`/`research_notes`,
  regardless of classification.
- The `$AUTO_MODE` "no ask" branch (nested under "The operator prompt") is gated only on
  non-empty evidence, not on the classification being `inconclusive`.
- The doc's intro paragraphs describe a two-way branch (prompt vs. no evidence), predating the
  three-outcome classification.

None of this requires new code: `format_verified_absent_evidence()` and
`format_verified_present_closure_note()` already exist in `check_brief_staleness.py` and are
already test-covered. The only gap is that the skill doc's control flow never calls for them.

## Goals / Non-Goals

**Goals:**
- Make the skill doc's documented control flow match the classification it already computes,
  for both interactive and `$AUTO_MODE` dispatch.
- Reuse the exact formatter names and command patterns (`worktrail-run-record append`,
  `worktrail-work-queue done`) the sibling predicate-recheck section already establishes for
  its structurally identical `still-true`/`resolved` outcomes, so the doc stays internally
  consistent rather than introducing a second prose style for the same shape of automatic
  outcome.

**Non-Goals:**
- No change to `check_brief_staleness.py`, `check_brief_predicate.py`, or any other source
  file — the formatters and their tests are already complete.
- No change to the canonical `openspec/specs/stale-brief-precheck/spec.md` — the requirements
  this change implements are already declared there.
- No change to the predicate re-check section ("Predicate re-check" / "still-true" /
  "resolved") — it already documents its own automatic outcomes correctly; this change only
  extends the analogous treatment to the file-state verification step below it.

## Decisions

**Mirror the predicate re-check section's structure rather than inventing new prose shape.**
The doc already has a working template for "automatic outcome, cite evidence, append/close,
stop" in the predicate re-check's `still-true` (append to run record post-Phase-6) and
`resolved` (close pre-Phase-6, stop) subsections. The `verifiably-absent` outcome is
structurally identical to `still-true` (proceed, cite evidence post-Phase-6); `verifiably-present`
is structurally identical to `resolved` (close pre-Phase-6, stop). Reusing that shape — same
command patterns, same "stop; do not continue to Phase 6" framing — means a reader who already
understands the predicate section immediately understands the verification section, and keeps
the two sibling automatic-outcome mechanisms visually consistent rather than each reading like a
one-off.

Alternative considered: write the three outcomes as a free-standing subsection with its own
narrative structure. Rejected — the doc already pays the cost of two near-identical automatic-
outcome mechanisms (predicate re-check and file-state verification) existing side by side;
diverging their prose shape for no functional reason would make the doc harder to scan, not
easier.

**Restructure "The operator prompt" as the `inconclusive`-only path, not a separate gate check
layered on top.** The existing heading and its `$AUTO_MODE` subsection remain the fallback for
`inconclusive`, but the file-state verification step's own writeup (already present) states the
gate directly: `verifiably-absent`/`verifiably-present` are handled inline right after
classification, and "The operator prompt" section is retitled/prefaced to make explicit it only
runs for `inconclusive` (and for the pre-existing case where verification never ran at all,
i.e. `checked:true` with all evidence lists empty — unchanged from today).

Alternative considered: leave "The operator prompt" as an unconditional section and add a new
early-return note above it. Rejected — the archived change's own tasks (2.3-2.5) already frame
this as three outcomes with the operator prompt being one of them, not a default with carve-outs
bolted on after the fact; matching that framing keeps the doc's stated contract legible without
an operator having to trace two separate gate conditions to find out when the prompt fires.

## Risks / Trade-offs

[A future reader skims only "The operator prompt" section and misses the new gate stated just
above it] → The gate is stated in the same place the archived change's task 2.5 specified
("continue to today's unmodified operator prompt section unchanged") — immediately before that
section, in the file-state verification write-up, not buried elsewhere in the doc.

[Doc drifts from the formatters' actual signatures if `check_brief_staleness.py` changes later]
→ Out of scope for this change (doc-only); the existing `tests/test_plugin_surface.py`-style
lockstep tests don't cover skill-doc prose against source signatures, so this is a pre-existing
risk class, not one this change introduces or worsens.
