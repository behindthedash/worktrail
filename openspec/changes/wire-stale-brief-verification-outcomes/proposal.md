## Why

The canonical `stale-brief-precheck` spec already declares two ADDED Requirements —
"Verified Absent Proceeds Automatically With Recorded Verification" and "Verified Present
Closes The Brief Automatically Citing The Verification" — and the formatting helpers they
depend on (`format_verified_absent_evidence`, `format_verified_present_closure_note` in
`src/worktrail/router/check_brief_staleness.py`) are implemented and fully test-covered. But
`skills/worktrail-go/references/brief-staleness-check.md`, the skill doc that actually drives
`/go` Phase 5.5 dispatch behavior, never wires the file-state verification classification it
already computes (`verifiably-absent` / `verifiably-present` / `inconclusive`) to any outcome:
"The operator prompt" section fires unconditionally on any non-empty evidence regardless of
classification, and the `$AUTO_MODE` "no ask" section is gated only on evidence being
non-empty, not on the classification being inconclusive. The archived change
`2026-08-17-stale-brief-precheck-verify-before-prompt` scoped exactly this doc wiring as tasks
2.3-2.7 but left them unticked — remediation review (2026-08-23) confirmed this is a genuine
remaining gap, not checkbox drift, and filed this change to close it.

## What Changes

- Document the `verifiably-absent` outcome in the skill doc: proceed to Phase 6/7 without an
  operator prompt, then append the `format_verified_absent_evidence()` string via
  `worktrail-run-record append "$RUN" decisions ...` once Phase 6 opens the run record.
- Document the `verifiably-present` outcome: close the brief automatically via
  `worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note ...` using
  `format_verified_present_closure_note()`, before Phase 6 opens a run record, report the
  closure, and stop.
- Document the `inconclusive` outcome as a no-op fall-through to the existing, unmodified
  operator prompt.
- Gate the `$AUTO_MODE` section's decision-record-plus-release path so it applies only when the
  verification outcome is `inconclusive` — `verifiably-absent`/`verifiably-present` resolve
  automatically in `AUTO_MODE` exactly as they do interactively, since the verification step is
  an internal reasoning step, not a human-facing prompt.
- Update the doc's intro paragraphs describing the probe-based flow to reflect the resulting
  three-outcome shape instead of the prior two-way (prompt vs. no-op) branch.

This is documentation-only. No code changes: both formatting helpers already exist and are
already fully test-covered (`tests/router/test_check_brief_staleness.py`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The canonical `stale-brief-precheck` spec already carries the target requirements this
change implements; no spec-level requirement is being introduced or changed here, only the
skill doc that was supposed to already reflect them.

## Impact

- `skills/worktrail-go/references/brief-staleness-check.md` — the only file touched.
- Supersedes tasks 2.3-2.7 of the archived change
  `openspec/changes/archive/2026-08-17-stale-brief-precheck-verify-before-prompt/tasks.md`,
  which assigned this exact scope but were never implemented.
- No `src/worktrail/**` changes, so the `CI: Version Bump Check` gate on `pyproject.toml`'s
  version line does not apply.
