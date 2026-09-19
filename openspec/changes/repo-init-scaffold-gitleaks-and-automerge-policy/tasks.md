## 1. Vendor the gitleaks signal-integrity script

- [x] 1.1 Add `src/worktrail/onboarding/gitleaks_template.py` holding
      `CHECK_GITLEAKS_SIGNAL_INTEGRITY_PY` as a string constant, vendored from
      worktrail's own `scripts/ci/check_gitleaks_signal_integrity.py` (itself the
      datalena reference copy). Match `rulesets_drift_guard_template.py`'s module
      docstring shape: name the source of truth and state that there is no automated
      sync back to it.
      (Requirement: Scaffold a portable gitleaks secrets-scan workflow.)
      files: src/worktrail/onboarding/gitleaks_template.py

## 2. Scaffold the workflow and its script

- [x] 2.1 [depends: 1.1] In `src/worktrail/onboarding/repo_init.py` add
      `GITLEAKS_WORKFLOW_RELPATH`, `GITLEAKS_SCRIPT_RELPATH`,
      `GITLEAKS_PR_DIFF_JOB_NAME = "gitleaks-pr-diff"` and
      `build_gitleaks_workflow(branches)`, rendering the two-job workflow the spec
      describes: a `pull_request`-triggered `gitleaks-pr-diff` job with no
      `paths`/`paths-ignore` and no change-detection gate, `fetch-depth: 0`, a
      `base.sha..head.sha` scan, and a signal-integrity step that is not
      `continue-on-error`; plus a `workflow_dispatch`-only full-history job. Pin
      actions to the versions already used elsewhere in this module.
      (Requirement: Scaffold a portable gitleaks secrets-scan workflow.)
      files: src/worktrail/onboarding/repo_init.py

- [x] 2.2 [depends: 2.1] Probe `gitleaks_workflow_exists` in `propose`'s `state`, and
      write the workflow plus the vendored script when absent / report them skipped
      when present, following the `rulesets_drift_guard` write-or-skip branch exactly.
      (Requirement: Scaffold a portable gitleaks secrets-scan workflow.)
      files: src/worktrail/onboarding/repo_init.py

## 3. Widen the required-check exception

- [x] 3.1 [depends: 2.2] Replace `propose`'s single `openspec_validate_newly_written`
      boolean with a list of newly-written required-check job names (openspec-validate
      and/or gitleaks-pr-diff), pass it to `build_ruleset_for_branch` for a fresh
      ruleset file, and call `patch_ruleset_required_check` once per name for an
      existing one. `build_ruleset_for_branch`'s `extra_required_status_check`
      parameter becomes a sequence; keep the "nothing from `ci_jobs_discovered` is ever
      auto-required" rule intact.
      (Requirement: Wire the new check into required_status_checks, scoped to this job only.)
      files: src/worktrail/onboarding/repo_init.py

## 4. Seed the policy default

- [x] 4.1 In `default_policy_yaml()`, emit `automerge:\n  enabled: true\n  max_risk: medium\n`
      after the header comments, composing with the existing `pre_commit_cmd` and
      `add_ons.aspens` keys. Update `skills/worktrail-repo-init/SKILL.md`'s Best
      Practices to state the seeded default instead of telling the operator not to
      guess automerge settings.
      (Requirement: Seed the fleet's auto-merge default into a scaffolded policy.)
      files: src/worktrail/onboarding/repo_init.py, skills/worktrail-repo-init/SKILL.md

## 5. Tests

- [x] 5.1 [depends: 3.1, 4.1] In `tests/onboarding/test_repo_init.py` cover every
      scenario in both spec deltas: the workflow is written on a fresh repo and skipped
      when present; the rendered YAML's `gitleaks-pr-diff` job carries no
      `paths`/`paths-ignore` and no `needs`; the full-history job is
      `workflow_dispatch`-gated; both job names land in a fresh ruleset and in a patched
      existing one; only the newly-written workflow's name is added; and
      `default_policy_yaml()` parses to the seeded automerge mapping in each of its
      argument combinations.
      (Requirements: Scaffold a portable gitleaks secrets-scan workflow, Wire the new check into required_status_checks, scoped to this job only, Seed the fleet's auto-merge default into a scaffolded policy.)
      files: tests/onboarding/test_repo_init.py

## 6. Verification

- [x] 6.1 [depends: 5.1] [e2e] Run `PYTHONPATH=src pytest -q tests/onboarding`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate repo-init-scaffold-gitleaks-and-automerge-policy --strict` and
      `worktrail-compile openspec/changes/repo-init-scaffold-gitleaks-and-automerge-policy`.
