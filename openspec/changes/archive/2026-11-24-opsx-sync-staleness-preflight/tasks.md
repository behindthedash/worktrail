## 1. Add the remote-baseline sync preflight

- [x] 1.1 In `skills/openspec-sync-specs/SKILL.md` and `commands/opsx/sync.md`, add the same
      mandatory preflight before any canonical-spec read or edit: fetch `origin`, resolve the
      caller-provided base or default `main`, and read canonical capability specs from
      `origin/<base>` as the merge baseline. Define an explicit failure-before-edit outcome for a
      failed fetch or unresolved remote base. For every affected delta block, compare the matching
      remote canonical requirement after `rstrip` normalization; if all are equal, report
      `already synced` and stop with no edits, while any missing, different, or unmatchable block
      proceeds through the existing intelligent merge against the remote content. Preserve the
      existing delta operation semantics, shape/no-restyle rules, validation, and summaries.
      In `tests/test_plugin_surface.py`, add assertions covering both instruction surfaces and
      rejecting removal of the fetch, `origin/<base>` baseline, edit-free already-synced outcome,
      or fetch-failure-before-edit rule.
      (Requirements: Sync preflight uses the remote canonical baseline; Already-converged remote delta is an edit-free no-op; Both bundled sync entry surfaces retain the staleness preflight)
  files: skills/openspec-sync-specs/SKILL.md commands/opsx/sync.md tests/test_plugin_surface.py

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/test_plugin_surface.py`, then
      `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, `python3 scripts/ci/ruff_pinned.py check .`, and
      `python3 scripts/ci/ruff_pinned.py format --check .`. Run `openspec validate
      opsx-sync-staleness-preflight --strict` and `worktrail-compile
      openspec/changes/opsx-sync-staleness-preflight`.
