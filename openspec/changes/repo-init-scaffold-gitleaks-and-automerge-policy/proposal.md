## Why

`~/rules/CLAUDE.repo.md` sections 2 and 3 name two things every fleet repo must
have, and `worktrail-repo-init` scaffolds neither, so every newly onboarded repo
starts below the baseline and needs a manual follow-up PR:

- **`gitleaks-pr-diff`** — a leaked secret must block merge, so the doctrine makes
  this a *required* status check on every protected branch, run on every PR with no
  paths filter (a required check a docs-only diff skips never reports and deadlocks
  the merge). It scans only `base.sha..head.sha` and fails when that range covered
  zero commits, so a degenerate range cannot pass identically to a clean scan.
  Reference implementation: `datalena`'s `.github/workflows/gitleaks.yml` plus
  `scripts/ci/check_gitleaks_signal_integrity.py`. Cross-repo `workflow_call` reuse
  is unavailable on this GitHub plan, so each repo carries its own copy — exactly
  the vendoring shape `repo_init` already uses for the rulesets drift guard and the
  Dependabot manifest check.
- **`automerge: {enabled: true, max_risk: medium}`** in `.worktrail/policy.yaml` —
  the fleet-wide default since devops PRs #510/#511. `default_policy_yaml()` emits
  header comments only, so a fresh repo's auto-merge workflow is inert until someone
  edits the file by hand, and `skills/worktrail-repo-init/SKILL.md` currently tells
  the operator not to guess automerge settings at all.

Verified 2026-09-18 (brief `20260918-195552-repo-init-scaffold-gitleaks-automerge`):
the auto-merge *workflow* needs no change — `build_automerge_workflow()` already has
the positive `go:risk-low|medium` gate, `labeled`/`unlabeled` triggers and the disarm
step. Only the policy seed and the gitleaks scaffold are missing.

## What Changes

- New `gitleaks_template.py` alongside `rulesets_drift_guard_template.py`, vendoring
  `scripts/ci/check_gitleaks_signal_integrity.py` as a string constant.
- `propose` writes `.github/workflows/gitleaks.yml` (per-PR diff scan + a
  `workflow_dispatch`-only full-history job) and the vendored integrity script when
  they are absent, and skips them when present — the same write/skip shape every
  other scaffolded file uses.
- `gitleaks-pr-diff` is wired into `required_status_checks` through the existing
  repo-init-ci-gate mechanism (fresh ruleset file and patch-an-existing-file), which
  until now admitted only `openspec-validate`.
- `default_policy_yaml()` seeds `automerge: {enabled: true, max_risk: medium}`, and
  `skills/worktrail-repo-init/SKILL.md`'s Best Practices says so instead of telling
  the operator not to guess.

## Capabilities

### New Capabilities
- `repo-init-policy-seed`: a scaffolded `.worktrail/policy.yaml` carries the fleet's
  auto-merge default rather than comments alone.

### Modified Capabilities
- `repo-init-ci-gate`: the required-check exception covers the secrets-scan job too.

## Impact

- `src/worktrail/onboarding/repo_init.py`, `src/worktrail/onboarding/gitleaks_template.py` (new).
- `skills/worktrail-repo-init/SKILL.md`.
- `tests/onboarding/test_repo_init.py`.
- No behavior change for an already-onboarded repo: every scaffolded path is
  write-if-absent, and the ruleset patch is additive and idempotent.
