## 1. Formatting helpers

- [x] 1.1 In `src/worktrail/router/check_brief_staleness.py`, add
      `format_verified_absent_evidence(matches, pull_requests, finding)` — builds the canonical
      evidence-line string (matched commits/PRs plus the verification finding) for the
      run-record-append pattern, mirroring `check_brief_predicate.format_still_true_evidence`'s
      shape and docstring style.
- [x] 1.2 In the same module, add
      `format_verified_present_closure_note(matches, pull_requests, finding)` — builds the
      canonical closure-note string for `work_queue.py done --note`, mirroring
      `check_brief_predicate.format_resolved_closure_note`'s shape and docstring style.
- [x] 1.3 Add `tests/router/test_check_brief_staleness.py` coverage for both helpers: matches
      only, pull requests only, both, and the exact rendered string shape (mirroring the
      existing `test_check_brief_predicate.py` coverage style for
      `format_still_true_evidence`/`format_resolved_closure_note`).
      <!-- Checkbox-drift remediation (20260823-211154): implemented pre-archive, never ticked.
           Verified: TestFormatVerifiedAbsentEvidence / TestFormatVerifiedPresentClosureNote in
           tests/router/test_check_brief_staleness.py cover matches-only, PRs-only, both, empty,
           and research-notes variants. 74 passed, 5 subtests passed. -->

## 2. Skill-doc verification step

<!-- Checkbox-drift remediation (20260823-211154): 2.3-2.7 verified as a genuine remaining gap,
     NOT checkbox drift. The living spec delta in this change's own specs/stale-brief-precheck/
     spec.md already carries the ADDED Requirements "Verified Absent Proceeds Automatically With
     Recorded Verification" and "Verified Present Closes The Brief Automatically Citing The
     Verification" -- but skills/worktrail-go/references/brief-staleness-check.md's "File-state
     verification" section (2.1/2.2, correctly ticked) only classifies into
     verifiably-absent/verifiably-present/inconclusive and never wires that classification to any
     outcome: "The operator prompt" fires unconditionally regardless of classification, and the
     $AUTO_MODE section is gated on evidence being non-empty, not on inconclusive. Left unchecked
     here per remediation discipline (do not tick unimplemented work); tracked in a new narrowly-
     scoped active OpenSpec change referencing this archived one -- see
     openspec/changes/wire-stale-brief-verification-outcomes/. -->


- [x] 2.1 In `skills/worktrail-go/references/brief-staleness-check.md`, insert a new section
      "File-state verification" implementing the requirement "File-State Verification Precedes Evidence Surfacing",
      between "Running it" and "Reading the result"/"The operator
      prompt", gated on `matches`/`pull_requests` being non-empty and the predicate re-check
      not having already decided the outcome. Instruct the dispatching agent to read/grep the
      brief's named paths and symbols for the specific capability its focus prose describes,
      and to classify the result as `verifiably-absent`, `verifiably-present`, or
      `inconclusive`, defaulting to `inconclusive` whenever the read is partial, ambiguous, or
      the capability is implemented differently than described.
      files: skills/worktrail-go/references/brief-staleness-check.md
- [x] 2.2 Update the "Reading the result" table's `checked: true`, non-empty-evidence row to
      point to the new verification step instead of straight to "the operator" prompt.
- [ ] 2.3 Document the "Verified Absent Proceeds Automatically With Recorded Verification"
      outcome: proceed to Phase 6/7 without a prompt, then
      append the `format_verified_absent_evidence` string via `worktrail-run-record append "$RUN"
      decisions "..."` once Phase 6 opens the run record — following the same post-Phase-6
      pattern the predicate re-check's `still-true` outcome already uses.
- [ ] 2.4 Document the requirement "Verified Present Closes The Brief Automatically Citing The Verification":
      close the brief automatically via
      `worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note "..."` using
      `format_verified_present_closure_note`, before Phase 6 opens a run record, report the
      closure, and stop — following the same pre-Phase-6 pattern the predicate re-check's
      `resolved` outcome already uses.
- [ ] 2.5 Document the `inconclusive` outcome as a no-op fall-through: continue to today's
      unmodified "The operator prompt" section unchanged.
- [ ] 2.6 Update the `$AUTO_MODE` section so its decision-record-plus-release path is reached
      only when the file-state verification outcome is `inconclusive` — `verifiably-absent`/
      `verifiably-present` resolve automatically in `AUTO_MODE` exactly as they do
      interactively, since the verification step is not a human-facing prompt.
- [ ] 2.7 Update the module-level docstring/description in
      `skills/worktrail-go/references/brief-staleness-check.md` (the intro paragraphs describing
      the probe-based flow) so it reflects the three-outcome shape instead of the prior two-way
      branch, consistent with the rest of the doc's existing style of documenting behavior
      up front.

## 3. Spec sync and verification

- [x] 3.1 Confirm `openspec/changes/stale-brief-precheck-verify-before-prompt/specs/stale-brief-precheck/spec.md`
      does not restate or contradict the search-boundary edits pending in
      `stale-brief-precheck-recheck-search-boundary` or
      `stale-brief-precheck-consolidation-original-created` (both scoped to the "History Search
      Is Bounded By The Brief's Capture Time" requirement, untouched by this change).
      files: openspec/changes/stale-brief-precheck-verify-before-prompt/specs/stale-brief-precheck/spec.md
- [x] 3.2 [e2e] Run `openspec validate --change stale-brief-precheck-verify-before-prompt --strict`
      and resolve any reported issues.
      <!-- Checkbox-drift remediation (20260823-211154): the CLI (openspec 1.8.0) has no
           --change flag (only --changes for bulk), and the archived change is not addressable
           by this slug post-archive ("Unknown item ..."). Substituted `openspec validate
           stale-brief-precheck --type spec --strict` -> "Specification 'stale-brief-precheck'
           is valid". No issues to resolve. -->
- [x] 3.3 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`; both green before
      this change is considered implementation-complete.
      <!-- Checkbox-drift remediation (20260823-211154): re-run now from the worktree root --
           `PYTHONPATH=src pytest -q`: 4401 passed, 2 skipped, 271 subtests passed. `orchestrate
           check`: GOLDEN OK. Also independently confirmed green on the implementation PRs'
           own CI at merge time (#510, #512, both "Lint, Test & Build": pass). -->
