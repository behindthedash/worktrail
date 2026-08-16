## Why

The Phase 5.5 brief-staleness guard (`stale-brief-precheck`) always files a human decision (or
prompts the operator) when it finds evidence a brief's named files/symbols were touched since
capture — even for briefs whose staleness question is already mechanically decidable. Decision
`20260814-030507-does-merged-pr-46-fix` (answered 2026-08-16) is a concrete instance: the guard
surfaced `behindthedash` PR #46 against checkbox-drift brief `20260805-201302` and filed a human
decision, even though the checkbox-drift-sweep predicate that generated the brief — "is
`status: completed` still unbacked by fully-checked body checkboxes?" — could answer the same
question deterministically by re-reading the two named task files, with no PR/commit correlation
guesswork involved. PR #46 never touched TASK-003/TASK-004, and both files' checkboxes were still
unchecked on disk; a human spent a decision-queue round-trip confirming what a four-line re-scan
would have shown directly.

## What Changes

- Add a **predicate re-check** step to Phase 5.5, gated on a brief carrying a
  `drift-source: checkbox-drift-sweep` frontmatter marker, that re-runs the checkbox-drift
  predicate (`checkbox_audit.audit_repo`) against the brief's originally captured findings
  **before** the existing probe-based `check_brief_staleness` search or operator prompt runs.
- Predicate still returns a hit for a finding (drift still present) → skip the probe search and
  prompt; proceed with the dispatch automatically, recording the predicate re-check result and
  the still-drifted findings on the run record once Phase 6 opens it.
- Predicate no longer returns a hit for any finding (all checkboxes now checked, or the file's
  `status:` moved off `completed`) → skip the probe search and prompt; close the brief
  automatically as already-delivered, citing the predicate re-check result itself as the reason
  — explicitly not any PR/commit evidence, which is coincidental correlation, not proof.
- No `drift-source` marker on the brief, an unrecognized `drift-source` value, or the re-check
  itself erroring (unreadable task file, missing `drift-findings`, exception) → fall through
  unchanged to today's probe-based `check_brief_staleness` search and operator-prompt/decision
  flow. This is the existing, unmodified behavior for every other brief.
- Stamp the checkbox-drift-sweep's captured findings onto the brief as structured, re-runnable
  frontmatter (`drift-findings:`, one entry per hit with at least `path`) instead of only prose
  bullets in the body, so the predicate re-check does not need to re-derive the finding set by
  parsing free text.
- The existing "never auto-applied" behavior for prose (non-predicate) briefs, and for any
  brief whose predicate re-check cannot run, is unchanged — this is a narrow, machine-checkable
  carve-out, not a general relaxation of operator-in-the-loop staleness decisions.

## Capabilities

### Modified Capabilities

- `stale-brief-precheck`: the "Evidence Is Surfaced To The Operator, Never Auto-Applied"
  requirement gains a narrow exception — a brief carrying a deterministic, machine-checkable
  staleness predicate is decided by re-running that predicate, bypassing the operator-prompt/
  human-decision path entirely, instead of always surfacing probe-matched evidence for a human
  judgment call. All other requirements in this capability (probe extraction, probe bounding,
  history search, PR lookup, fail-open, per-route coverage) are unchanged; this change only adds
  a new requirement for the predicate re-check itself and narrows one existing requirement's
  scope to name the carve-out explicitly.

## Impact

- `src/worktrail/router/check_brief_staleness.py` or a new sibling module — add the predicate
  re-check entrypoint and a registry keyed by `drift-source` value (starting with
  `checkbox-drift-sweep`, backed by `taskformats/devkit/checkbox_audit.audit_repo`).
- `src/worktrail/router/spec_sync_sweep_checkbox_brief.py` — stamp structured `drift-findings:`
  frontmatter (currently only rendered as prose body bullets) on captured checkbox-drift briefs.
- `skills/worktrail-go/references/brief-staleness-check.md` — document the new predicate
  re-check sub-branch and its two auto-resolving outcomes ahead of the existing "Running it" /
  "Reading the result" / operator-prompt sections, which stay as the fallback path.
- `src/worktrail/router/run_record.py` — no schema change; the existing `decisions` append
  pattern already used for the "proceed" outcome is reused to record predicate re-check
  evidence.
- Test coverage: `tests/router/test_check_brief_staleness.py` (or a new predicate-recheck test
  module), `tests/router/test_spec_sync_sweep_checkbox_brief.py` for the new frontmatter field.
