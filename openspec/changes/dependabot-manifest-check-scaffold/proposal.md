## Why

A `.github/dependabot.yml` `update` entry whose `directory:` points at a location containing no
manifest Dependabot can parse fails the *Dependabot Updates* job, not CI — there is no PR check,
no branch-protection signal, and no notification. The repo simply stops receiving dependency
updates for that ecosystem, invisibly, until someone happens to open the Actions tab. This exact
failure was just fixed in `behindthedash/devops` (PR #306), whose remedy —
`scripts/test_dependabot_config.py` — is a devops-local regression test that only ever inspects
that one repo's own `dependabot.yml`. Every other repo in the fleet is still exposed, and the
doctrine (`~/rules/CLAUDE.repo.md` §4) tells every repo to grow a `dependabot.yml`, so the
exposure grows with onboarding.

`worktrail-repo-init` already solved the structurally identical problem for branch-protection
rulesets: rather than a central cross-repo sweep, `propose` scaffolds a self-contained per-repo
CI check (`rulesets-drift-guard-scaffold`) that runs at PR/review time in the onboarded repo.
This change makes the same shift for Dependabot config validity.

## What Changes

- **New scaffolded artifact pair, written by `worktrail-repo-init propose`**, mirroring the
  rulesets drift guard exactly:
  - a vendored script `scripts/ci/dependabot/dependabot_manifest_check.py` plus its
    `scripts/ci/dependabot/requirements.txt`, emitted from a new
    `src/worktrail/onboarding/dependabot_manifest_check_template.py` string constant (same
    "inline template, no package-data resolution at runtime" posture as
    `rulesets_drift_guard_template.py`);
  - a workflow `.github/workflows/dependabot_manifest_check.yml` targeting the repo's actual
    branch model (`dev`/`prd` or `dev`/`stg`/`prd`), never a hardcoded `main`.
- Both are written **write-if-absent idempotent**, reported under `written`/`skipped`, and
  surfaced in `detect_state()` — identical to every other artifact `propose` scaffolds.
- **The check itself** reads the target repo's `.github/dependabot.yml` and, for each `update`
  entry whose `package-ecosystem` is in a known ecosystem→manifest-glob table (`pip`, `npm`),
  asserts the configured directory actually contains a matching manifest. Ecosystems absent from
  the table (e.g. `github-actions`, which scans `.github/workflows/` and cannot fail this way)
  are skipped, never failed. A repo with no `.github/dependabot.yml` is a clean pass.
- **Documentation**: `skills/worktrail-repo-init/SKILL.md` gains the new artifact alongside its
  existing description of the drift-guard scaffold.

### Explicitly not in this change

- **No fleet-wide rollout.** Applying the new scaffold to already-onboarded repos across
  `~/projects/*` is separate follow-up work in `devops`, once this capability exists. That
  includes worktrail's own repo — it is itself an already-onboarded repo, so dogfooding the
  scaffold into `worktrail/scripts/ci/dependabot/` belongs to the same rollout, not here. The
  vendored script's behavior is still fully unit-tested here, against the template constant.
- **No `.github/dependabot.yml` scaffolding.** `propose` does not write a `dependabot.yml` today
  and will not start here. *Consequence worth stating plainly:* in a freshly onboarded repo with
  no `dependabot.yml`, the scaffolded check is a passing no-op until someone adds one. The check
  is still correct to scaffold now — it is what makes the eventual `dependabot.yml` safe — but
  the pair "scaffold `dependabot.yml` too" is a real, separate follow-up.
- **No `required_status_checks` wiring.** The existing `repo-init-ci-gate` capability states that
  `propose` SHALL NOT add any CI job other than `openspec-validate` to `required_status_checks`.
  This change honors that: the new job surfaces as an ordinary failing PR check. Promoting it to
  a required check would amend `repo-init-ci-gate` and is a separate decision.

## Capabilities

### New Capabilities
- `dependabot-manifest-check-scaffold`: `worktrail-repo-init propose` scaffolds a per-repo CI
  check — vendored script plus workflow, write-if-absent idempotent, branch-model-aware — that
  validates every checkable ecosystem entry in the target repo's `.github/dependabot.yml` has a
  real manifest under its configured directory.

### Modified Capabilities

None. `repo-init-ci-gate`'s requirements are deliberately left intact (see "No
`required_status_checks` wiring" above), and `rulesets-drift-guard-scaffold` is the pattern
being mirrored, not changed.

## Impact

- **New file**: `src/worktrail/onboarding/dependabot_manifest_check_template.py` (vendored
  script + requirements string constants).
- **Modified**: `src/worktrail/onboarding/repo_init.py` — new relpath constants, a
  `build_dependabot_manifest_check_workflow(branches)` builder, two new `detect_state()` keys,
  and three write-if-absent blocks in `cmd_propose()`.
- **Modified**: `skills/worktrail-repo-init/SKILL.md` (Overview + Step 2 narrative).
- **New tests**: `tests/onboarding/test_repo_init.py` additions (workflow shape, branch-model
  substitution, write/skip idempotency) and a new `tests/onboarding/test_dependabot_manifest_check.py`
  exercising the vendored script's logic directly.
- **No new runtime dependency for worktrail.** The scaffolded script needs `pyyaml` in the
  *target* repo's CI, declared in the vendored `scripts/ci/dependabot/requirements.txt`; worktrail
  already depends on `pyyaml` itself, so its own tests can exec the script unchanged.
- **No GitHub API access, no credentials.** Unlike the rulesets drift guard, this check is purely
  filesystem + YAML, so it needs no GitHub App token and has no "missing credentials" skip path.
