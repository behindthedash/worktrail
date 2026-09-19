## Context

`repo_init.propose` already scaffolds four CI artifacts (auto-merge, openspec-validate,
rulesets drift guard, Dependabot manifest check) with one shape: a `*_RELPATH` constant,
a `build_*` renderer, a `state[...]_exists` probe, and a write-if-absent/skip-if-present
branch in `propose`. Two of them vendor a helper script from a `*_template.py` module
as string constants rather than package data, so the CLI has no runtime dependency on
package-data resolution. This change adds a fifth artifact in that same shape.

## Goals / Non-Goals

- Goal: a repo that has just been through `worktrail-repo-init` satisfies
  `~/rules/CLAUDE.repo.md` sections 2 and 3 without a manual follow-up PR.
- Goal: idempotent and additive — running `propose` again, or running it on an
  already-onboarded repo, changes nothing it did not write.
- Non-goal: changing `build_automerge_workflow()`. Verified 2026-09-18 that it already
  meets the doctrine.
- Non-goal: back-filling existing repos. That is a fleet rollout, tracked separately as
  brief `20260918-192959-fleet-rollout-gitleaks-automerge-standards`.
- Non-goal: a full-history scan on every PR. The full-history job is
  `workflow_dispatch`-only and never required, matching the reference implementation.

## Decisions

- **Two jobs, one file.** `gitleaks.yml` carries both the per-PR `gitleaks-pr-diff`
  job and the `workflow_dispatch`-gated `gitleaks-full-history` job, mirroring
  datalena's file so an operator comparing the two sees the same layout.
- **No paths filter, no `Detect changes` gate on the PR job.** A required check that a
  docs-only diff skips never reports a status and deadlocks the merge. This is the one
  scaffolded workflow that deliberately runs on every PR.
- **Signal integrity is a job step, not `continue-on-error`.** `gitleaks` reports "no
  leaks found" identically for a clean scan and for a scan that covered zero commits;
  the vendored `check_gitleaks_signal_integrity.py` fails the job on the latter.
- **Widen the required-check exception rather than add a parallel mechanism.**
  `repo-init-ci-gate` already owns "which job names `propose` may put in
  `required_status_checks`". Adding a second, differently-shaped path for the gitleaks
  job would give the same rule two homes. The list of admitted job names becomes a
  constant, and the newly-written flags become per-artifact instead of one boolean.
- **Seed the policy value, do not compute it.** `automerge: {enabled: true, max_risk:
  medium}` is the fleet default, not a per-repo judgment; `policy.py` already clamps an
  invalid `max_risk` to `low` and never treats `high`/`critical` as eligible whatever
  the file says, so seeding the documented default carries no new risk.

## Risks / Trade-offs

- A repo with real secrets in history gets a red `gitleaks-pr-diff` on its first PR.
  That is the intended signal; the full-history job exists to triage it, and the
  `.gitleaks.toml` allowlist (value-scoped, never path-scoped) is the documented
  remedy for a synthetic fixture.
- Seeding `automerge.enabled: true` means a freshly onboarded repo can merge a
  `go:risk-low`-labeled PR without a human. The label is applied only by worktrail's
  own classifier, and `propose` already warns when it writes the auto-merge workflow
  with no required checks configured — that warning now fires far less often, because
  this change gives every scaffolded repo a required check.
