## Why

`~/rules/CLAUDE.repo.md` section 3 lists a rulesets drift guard (verifies `.github/rulesets/*.json`
matches GitHub's live ruleset state) as a "Recommended" default CI job for every repo, citing
devops's `rulesets_drift_guard.yml` as the reference. `worktrail-repo-init propose`/`apply`
already scaffolds `.github/rulesets/*.json`, `.worktrail/policy.yaml`, an OpenSpec scaffold, and
an auto-merge workflow, but never scaffolds this job (confirmed absent from `repo_init.py`, see
`docs/specs/research/rulesets-drift-guard-not-scaffolded-by-repo-init.md`, PR #634). Every repo
onboarded via `repo-init` therefore commits rulesets JSON with nothing to catch an out-of-band
GitHub UI edit before it silently diverges.

## What Changes

- `propose` gains a vendored `scripts/ci/rulesets/{rulesets_sync.py,requirements.txt}` copy
  (kept as a template maintained in this repo, mirrored from devops's canonical version) plus a
  scaffolded `.github/workflows/rulesets_drift_guard.yml`, written alongside the existing
  rulesets JSON and the auto-merge workflow. Both are skipped (not overwritten) when already
  present, matching every other `propose` step's idempotency.
- The scaffolded workflow's `push`/`pull_request` branch triggers follow the repo's actual
  branch model (`dev`/`prd` or `dev`/`stg`/`prd`, from the same `branches` list `propose` already
  computes for the rulesets JSON) instead of hardcoding `main`, since a `repo-init`-onboarded
  repo never keeps `main` as a protected branch.
  It mints its App token from the same `vars.RELEASE_NOTES_APP_ID` /
  `secrets.RELEASE_NOTES_APP_PRIVATE_KEY` identity devops/datalena/GGB already use (no new
  GitHub App needed) via `actions/create-github-app-token`.
- The scaffolded workflow's job gains a credential-guard step: when
  `vars.RELEASE_NOTES_APP_ID`/`secrets.RELEASE_NOTES_APP_PRIVATE_KEY` are not yet configured in
  the target repo, later steps are skipped and the job reports a clean skip rather than the red-X
  failure GGB's first attempt produced (`e7ddad0f`, invalid `administration` permission on the
  default `GITHUB_TOKEN`, fixed by `18f024ac`'s switch to the App-token pattern).
- `apply` gains a check (via `gh variable list` / `gh secret list`) for whether
  `RELEASE_NOTES_APP_ID`/`RELEASE_NOTES_APP_PRIVATE_KEY` already exist in the target repo's
  Actions settings, and prints a one-line reminder to install the App and set them when they're
  missing -- `apply` already prints a "Manual follow-up" block for other post-merge steps, this
  joins it.
- Does **not** build a new cross-repo `workflow_call` pattern -- the discovery note found no
  existing precedent for one and judged it not worth the added design/testing cost now; this
  stays a per-repo vendored copy, matching the fleet's existing 3-of-3 pattern (devops, datalena,
  GGB).

## Capabilities

### New Capabilities
- `rulesets-drift-guard-scaffold`: `worktrail-repo-init propose`/`apply` scaffolds a working,
  credential-guarded rulesets drift-guard CI job (vendored sync script + workflow) for every
  onboarded repo, with a clean-skip posture and an apply-time reminder when App credentials
  aren't configured yet.

### Modified Capabilities
(none -- this is additive scaffolding; no existing spec's requirements change)

## Impact

- `src/worktrail/onboarding/repo_init.py`: new vendored-template constants/functions for the
  sync script, requirements file, and drift-guard workflow; wiring into `cmd_propose`
  (write-if-absent, same as the auto-merge workflow) and `cmd_apply` (credential-existence check
  + reminder).
- New template source under this repo (e.g. alongside `repo_init.py` or in a small
  `templates/` module) holding the vendored `rulesets_sync.py`/`requirements.txt` content kept in
  sync with devops's canonical copy per its own README's cross-repo vendoring note.
- `tests/onboarding/test_repo_init.py`: new tests for the scaffolded files' idempotency, branch-
  model-aware trigger generation, the credential-guard step's presence, and `apply`'s
  credential-check/reminder path (mocked `gh` calls).
- `skills/worktrail-repo-init/SKILL.md`: documents the new scaffolded files and the App
  install/credential reminder.
- No changes to devops, datalena, or GGB -- their existing copies are the reference, not a
  dependency.
