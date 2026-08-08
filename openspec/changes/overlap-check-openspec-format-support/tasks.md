## 1. Core scan() extension

- [ ] 1.1 In `src/worktrail/router/overlap_check.py`, add a helper that
      detects an OpenSpec-shaped root (has a `changes/` and/or `specs/`
      subdirectory) and branches `scan()` to a new OpenSpec extraction path
      instead of the existing `^\d{3,}-` devkit iteration, leaving the
      devkit path's code and output byte-for-byte unchanged.
- [ ] 1.2 Add extraction for `changes/*/proposal.md`: pull the `##
      Capabilities` section body, falling back to the first sentence of
      `## Why` when Capabilities is absent/empty; `feature_summary: null`
      when neither section has content.
- [ ] 1.3 Add extraction for `specs/*/spec.md`: pull the `## Purpose`
      section body as `feature_summary`.
- [ ] 1.4 Set `stage: "active"` for every `changes/*` entry and `stage:
      "complete"` for every `specs/*` entry; every OpenSpec entry's
      `user_request_excerpt` is `null` (no OpenSpec equivalent).
- [ ] 1.5 Ensure every OpenSpec-sourced entry has the identical dict key
      set as a devkit-sourced entry (`spec_id`, `stage`, `title`,
      `feature_summary`, `user_request_excerpt`).

## 2. Tests

- [ ] 2.1 Unit tests for the OpenSpec-shape detection helper (changes-only,
      specs-only, both, neither).
- [ ] 2.2 Unit tests for Capabilities/Why extraction: Capabilities present
      → used; Capabilities empty + Why present → Why used; neither present
      → `feature_summary: null`, no crash.
- [ ] 2.3 Unit tests for Purpose extraction from `specs/*/spec.md`.
- [ ] 2.4 `scan()` tests against a synthetic OpenSpec root covering
      changes-only, specs-only, and both-present layouts, asserting stage
      values and key-set parity with a devkit entry.
- [ ] 2.5 Regression test asserting `scan()` against an existing devkit
      fixture returns identical output to before this change (guards
      Requirement: Devkit-Shaped Root Scanning Is Unchanged).

## 3. Caller update

- [ ] 3.1 Update the `#overlap-check` procedure in
      `skills/worktrail-go/references/subagent-prompts.md` to invoke
      `worktrail-overlap-check` once per spec root that exists under
      `$REPO` (`$REPO/docs/specs` and/or `$REPO/openspec`) and merge the
      resulting `specs` arrays before the comparison step, instead of the
      current single hard-coded `--root "$REPO/docs/specs"` call.
- [ ] 3.2 [e2e] Manually verify `worktrail-overlap-check --root
      <worktrail-repo>/openspec --json` returns a non-empty `specs` array
      against this repo's own `openspec/` tree — the exact reproduction
      from the originating handoff brief.

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` — full suite green.
- [ ] 4.2 [e2e] Run `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` — golden record/replay
      regression green.
