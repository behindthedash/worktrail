# Tasks

## 1. Main-only branch model

- [x] 1.1 In `src/worktrail/onboarding/repo_init.py`: add `"main"` to `--branch-model`
      choices; teach `build_ruleset_for_branch()` to return a `protect-main` ruleset
      (`refs/heads/main`, `["squash"]`, `linear_history=True`); make `propose`/drift use
      `["main"]` as the branch list for model `main`; in `cmd_apply`, detect model `main`
      when the declared set is exactly `{"main"}`, reject a set mixing `main` with
      `dev`/`prd`, and for model `main` skip branch create/rename/default-flip, failing
      closed with a non-zero exit if the current default branch is not `main`, then apply
      delete-branch-on-merge, rulesets, and labels unchanged. Extend
      `tests/onboarding/test_repo_init.py`: main ruleset shape; `propose --branch-model main`
      writes only `protect-main.json`; main-only apply calls no create/rename/set-default
      helpers and applies the ruleset; non-`main` default errors; mixed declarations error;
      existing `2`/`3` tests still pass.
      (Requirement: Main-only branch model is a supported repo-init choice)
      (Requirement: Apply on a main-only repo never migrates branches)
      files: src/worktrail/onboarding/repo_init.py tests/onboarding/test_repo_init.py

- [x] 1.2 Document `--branch-model main` (trunk-only repos, no branch migration on apply)
      in `skills/worktrail-repo-init/SKILL.md`, including the usage line.
      (Requirement: Main-only branch model is a supported repo-init choice)
      files: skills/worktrail-repo-init/SKILL.md

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/onboarding/test_repo_init.py`, then
      `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`,
      and confirm all pass.
      depends: 1.1, 1.2
