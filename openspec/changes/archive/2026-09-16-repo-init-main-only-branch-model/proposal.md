## Why

`worktrail-repo-init` can only bootstrap repos onto a `dev`/`prd` (model `2`) or
`dev`/`stg`/`prd` (model `3`) branch model. Many repos (worktrail itself included) work
trunk-only on `main`, but there is no way to onboard them without migrating their branches:
`build_ruleset_for_branch()` in `src/worktrail/onboarding/repo_init.py` raises
`ValueError(f"unknown branch {branch!r}")` for anything other than `dev`/`stg`/`prd`,
`--branch-model` accepts only `choices=("2", "3")`, and `cmd_apply` refuses to run unless
both `protect-dev.json` and `protect-prd.json` exist, then unconditionally creates `dev`,
renames the current default branch to `prd`, and sets `dev` as the default. Applying that
flow to a main-only repo would be destructive. (Source: work-queue brief
`20260915-140217-worktrail-repo-init-has-no`.)

## What Changes

- `--branch-model` gains a `main` choice (alongside `2` and `3`) on `propose`/`check`/drift.
- `build_ruleset_for_branch()` handles `main`: a `protect-main` ruleset with squash-only
  merges and linear history.
- `propose --branch-model main` writes only `.github/rulesets/protect-main.json` and computes
  drift against `main` only.
- `cmd_apply` detects a main-only repo (`protect-main.json` declared, no `protect-dev.json`/
  `protect-prd.json`) and takes a new path: no branch creation, no rename, no default-branch
  change — it requires the current default branch to already be `main` (fail-closed error
  otherwise), then applies delete-branch-on-merge, rulesets, and labels exactly as today.
- A mix of `protect-main.json` with `protect-dev`/`protect-prd` is rejected as ambiguous.

## Capabilities

### New Capabilities

- `repo-init-main-only-branch-model`: onboarding a trunk-only repo onto `main` without
  branch migration.

### Modified Capabilities

- None.

## Impact

- `src/worktrail/onboarding/repo_init.py`: `build_ruleset_for_branch()`, `propose`/drift
  branch lists, `cmd_apply` branch-model detection and control flow, argparse choices.
- `tests/onboarding/test_repo_init.py`: coverage for the main-only ruleset, propose output,
  and the non-migrating apply path.
- `skills/worktrail-repo-init/SKILL.md`: document `--branch-model main`.
- Existing `2`/`3` behavior is unchanged.
