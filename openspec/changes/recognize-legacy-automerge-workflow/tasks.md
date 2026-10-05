## 1. Legacy-aware workflow selection and coverage

- [ ] 1.1 In `src/worktrail/onboarding/repo_init.py`, define the legacy
      `.github/workflows/auto-merge.yml` path and centralize effective auto-merge workflow
      selection: either recognized path prevents `propose` from writing the canonical workflow;
      legacy-only skips name the legacy path; canonical is selected for fresh and dual-path repos.
      Retain the aggregate `automerge_workflow_exists` state key while adding per-path state as
      needed. Make `compute_drift` compare every existing recognized path and make `apply` ensure
      `AUTOMERGE_LABELS` when either exists. In
      `pull_requests_doc_template.py` replace the hard-coded workflow path with a marker, and
      let `build_pull_requests_doc` render a selected path (canonical by default); pass the
      effective path only when `cmd_propose` writes an absent doc. Preserve write-if-absent,
      report-only drift, and never delete or rewrite either workflow. In
      `tests/onboarding/test_repo_init.py`, cover legacy-only `propose` (no canonical duplicate,
      byte-identical skip and legacy-aware new doc), fresh canonical creation, dual-path
      preservation/canonical prose preference, legacy and independent dual-path drift, and
      legacy-only `apply` label provisioning.
      (Requirements: propose recognizes the legacy auto-merge workflow; recognized auto-merge
      workflows participate in drift reporting; new PR-label guidance identifies the effective
      workflow; Content-only comparison for workflow and script templates; propose scaffolds the
      PR labels doc)
      files: src/worktrail/onboarding/repo_init.py, src/worktrail/onboarding/pull_requests_doc_template.py, tests/onboarding/test_repo_init.py

## 2. Operator documentation

- [ ] 2.1 Update `skills/worktrail-repo-init/SKILL.md` to state that `propose` recognizes the
      legacy `.github/workflows/auto-merge.yml` as well as the canonical
      `worktrail-auto-merge.yml`, does not create a duplicate or migrate either, and scaffolds
      new PR-label guidance for the effective existing workflow.
      (Requirement: propose recognizes the legacy auto-merge workflow)
      files: skills/worktrail-repo-init/SKILL.md

## 3. Verification

- [ ] 3.1 [depends: 1.1, 2.1] [e2e] Run `PYTHONPATH=src pytest -q
      tests/onboarding/test_repo_init.py`, then `PYTHONPATH=src pytest -q`; run
      `python3 scripts/ci/ruff_pinned.py check .` and
      `python3 scripts/ci/ruff_pinned.py format --check .`; then run `openspec validate
      recognize-legacy-automerge-workflow --strict` and `worktrail-compile
      openspec/changes/recognize-legacy-automerge-workflow`.
