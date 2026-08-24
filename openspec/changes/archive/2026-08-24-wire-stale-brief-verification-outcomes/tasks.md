## 1. Wire the verification classification to outcomes

- [x] 1.1 In `skills/worktrail-go/references/brief-staleness-check.md`, in the "File-state
      verification" section, after the classification table, document the `verifiably-absent`
      outcome: proceed to Phase 6/7 without an operator prompt, then append the
      `format_verified_absent_evidence()` string via `worktrail-run-record append "$RUN"
      decisions "..."` once Phase 6 opens the run record — mirroring the predicate re-check's
      `still-true` subsection's post-Phase-6 pattern.
      files: skills/worktrail-go/references/brief-staleness-check.md
- [x] 1.2 In the same section, document the `verifiably-present` outcome: close the brief
      automatically via `worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note
      "..."` using `format_verified_present_closure_note()`, before Phase 6 opens a run record,
      report the closure, and stop — mirroring the predicate re-check's `resolved` subsection's
      pre-Phase-6 pattern.
      files: skills/worktrail-go/references/brief-staleness-check.md
- [x] 1.3 In the same section, document the `inconclusive` outcome as a no-op fall-through:
      continue to "The operator prompt" section unchanged.
      files: skills/worktrail-go/references/brief-staleness-check.md
- [x] 1.4 Retitle/preface "The operator prompt" section so it states explicitly that it now
      runs only for the `inconclusive` classification (and the pre-existing case where
      verification never ran because there was no evidence to verify) — not unconditionally on
      any non-empty evidence.
      files: skills/worktrail-go/references/brief-staleness-check.md
- [x] 1.5 Update the `$AUTO_MODE` "no ask" subsection so its decision-record-plus-release path
      is reached only when the file-state verification outcome is `inconclusive` —
      `verifiably-absent`/`verifiably-present` resolve automatically in `AUTO_MODE` exactly as
      they do interactively, since the verification step is not a human-facing prompt.
      files: skills/worktrail-go/references/brief-staleness-check.md
- [x] 1.6 Update the doc's intro paragraphs (the module-level description of the probe-based
      flow) to reflect the resulting three-outcome shape (`verifiably-absent` /
      `verifiably-present` / `inconclusive`) instead of the prior two-way (prompt vs. no
      evidence) branch, consistent with the doc's existing style of documenting behavior up
      front.
      files: skills/worktrail-go/references/brief-staleness-check.md

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm it stays green — no source files
      changed by this doc-only change, so this confirms the edit introduced no accidental
      regression (e.g. a broken cross-reference `test_plugin_surface.py` checks).
- [x] 2.2 [e2e] Re-read the edited sections of
      `skills/worktrail-go/references/brief-staleness-check.md` end to end and confirm the
      three outcomes (`verifiably-absent`, `verifiably-present`, `inconclusive`) are each
      reachable, mutually exclusive, and that the `$AUTO_MODE` gate correctly reads only
      `inconclusive` falls through to decision-record-plus-release.
