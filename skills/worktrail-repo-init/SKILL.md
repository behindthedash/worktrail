---
name: worktrail-repo-init
description: >
  Bootstrap a new repo, or migrate an existing single-branch repo, onto the
  workspace's repo-standards doctrine (~/rules/CLAUDE.repo.md): the
  AGENTS.md-is-truth/CLAUDE.md-imports-it split, a dev/prd or dev/stg/prd
  branch model with matching GitHub rulesets, an OpenSpec scaffold, an
  auto-merge workflow, and a seeded .worktrail/policy.yaml. Trigger phrases: "onboard this
  repo", "apply repo standards", "initialize repo standards", "set up this
  repo like the rest of the fleet", "bring this repo into line with the repo
  standards doctrine".
argument-hint: "propose --repo <path> [--branch-model 2|3] | apply --repo <path>"
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# Repo Init Skill

## Overview

Applies the workspace's repo-standards doctrine to one repo at a time: the
`CLAUDE.md` → `@AGENTS.md` split, a branch model with matching
`.github/rulesets/*.json`, an OpenSpec scaffold,
`.github/workflows/worktrail-auto-merge.yml`,
`.github/workflows/rulesets_drift_guard.yml` (plus its vendored
`scripts/ci/rulesets/rulesets_sync.py` + `requirements.txt`), and a seeded
`.worktrail/policy.yaml`. The CLI (`worktrail-repo-init`) owns
file generation and the GitHub API calls; this skill owns the git workflow
around it (worktree, commit, PR) and the judgment calls the CLI deliberately
leaves to a human — see `references/branch-model-decision.md`.

## When to Use

Any repo under `~/projects/` that doesn't yet follow the doctrine: no
`AGENTS.md`, no `.github/rulesets/`, single-branch (`main`/`master` only, no
`dev`). Works for both a brand-new repo and an existing repo being migrated —
`propose` only writes files that don't already exist, so re-running it on a
partially-onboarded repo is safe.

## Instructions

### Step 1 — Decide the branch model

Read `references/branch-model-decision.md` before choosing `--branch-model`.
The short version: **2 (dev/prd) is the default** — use 3 (dev/stg/prd) only
when the repo has a real, distinct pre-production deployed environment to
gate against (matches datalena's pattern, enforced by its own
`promotion-target-guard` CI check). A repo with no deployment, or one whose
CI/CD already provides an equivalent gate (e.g. Vercel preview deployments
per-PR), stays at 2. If it's not obvious from the repo's README/AGENTS.md
which applies, ask the user with `AskUserQuestion` rather than guessing —
this changes the shape of every `.github/rulesets/*.json` file generated.

### Step 2 — Create a worktree and propose

`worktrail-repo-init` never touches git state itself (no worktree, no
commit, no push) — same division of responsibility as every other worktrail
CLI. Branch off the target repo's current default branch first:

```bash
cd ~/projects/<repo>
git worktree add ../<repo>-worktrees/repo-standards -b repo-standards <current-default-branch>
cd ../<repo>-worktrees/repo-standards
worktrail-repo-init propose --repo . --branch-model 2 --json
```

Review the JSON result's `written`/`skipped`/`warnings`. If `ci_jobs_discovered`
is non-empty, decide with the user which of those job display names (not
workflow filenames — see doctrine section 3) should actually gate merges, and
hand-edit the generated `.github/rulesets/*.json` files to add them to
`required_status_checks` before committing — `propose` deliberately never
does this automatically (a wrongly-required informational/flaky job would
deadlock every future PR).

`propose` also scaffolds `.github/workflows/rulesets_drift_guard.yml`, a
scheduled/PR-triggered workflow that runs the vendored
`scripts/ci/rulesets/rulesets_sync.py` to check (and, on `main`, apply) drift
between the committed `.github/rulesets/*.json` files and the rulesets
actually configured on GitHub. It mints its own GitHub App token for the
rulesets API calls rather than using `secrets.GITHUB_TOKEN`; nothing to
configure at `propose` time, but see Step 4 for the App credentials it needs
at runtime.

Ask the user whether to also pass `--with-aspens` (declares `add_ons.aspens`
in the seeded policy file and runs `aspens doc init` immediately, instead of
waiting for the repo's first orchestrated task) — don't default it on. There
is no equivalent flag for GitNexus: indexing/cron registration lives entirely
in `devops`'s own tooling (`.claude/skills/gitnexus-index-maintenance/`) with
no bridge into worktrail today; capture it as a `worktrail-handoff` brief if
it comes up rather than improvising something here.

### Step 3 — Commit, push, open the PR

Standard worktree workflow: `git add`, commit, push, `gh pr create` targeting
the repo's current default branch. State in the PR body that a follow-up
`worktrail-repo-init apply` run will create `dev`/`stg`, rename the current
default to `prd`, and flip the GitHub default branch to `dev` — reviewers
should know the branch structure is about to change underneath this PR.

### Step 4 — Apply, after the PR merges

This step mutates live GitHub state (branch creation/rename, default branch,
delete-branch-on-merge, branch protection) — **get explicit user confirmation
before running it**, especially on a public repo. Run from the canonical
checkout (not the worktree, which may be torn down already):

```bash
cd ~/projects/<repo>
git checkout <old-default-branch> && git pull
worktrail-repo-init apply --repo . --json
```

Report the result's `branches`/`default_branch`/`delete_branch_on_merge`/
`rulesets` status lines and the manual-follow-up checklist (retarget other
open PRs onto `dev`,
`git fetch && git switch dev` in every local clone). Any `FAILED` entry means
`apply` exited non-zero — investigate and re-run; it is safe to re-run (it
skips branches/renames already done, and PUT-then-reverifies rulesets
whether or not they already exist).

If the repo's `RELEASE_NOTES_APP_ID` variable and
`RELEASE_NOTES_APP_PRIVATE_KEY` secret aren't both already set, `apply`
prints a warning that the scaffolded `rulesets_drift_guard.yml` will skip
until the release-notes GitHub App is installed on the repo and those two
credentials are configured — pass this reminder on to the user rather than
treating it as a failure.

## Examples

**New repo, no CI, no deployment (e.g. a Windows desktop app distributed via
GitHub Releases):** `--branch-model 2`. `ci_jobs_discovered` comes back empty
— the generated rulesets carry zero required status checks, which is correct
until real CI exists.

**Existing repo with a Vercel-deployed frontend and per-PR preview
deployments:** `--branch-model 2` — Vercel's preview URL already gives a
pre-merge look at the deployed result; a persistent `stg` branch would
duplicate that without adding a real gate.

## Best Practices and Constraints

- `propose` is idempotent by file existence — it never overwrites a file
  that's already there, so re-running it after partial completion (or after
  a human hand-edited a generated ruleset) is safe.
- Never run `apply` speculatively "to see what happens" — it renames the
  live default branch and rewrites branch protection. Confirm with the user
  first, always, even under an otherwise-autonomous session.
- The OpenSpec scaffold is `--tools none` deliberately: worktrail's own
  plugin already bundles the `/opsx:*` Claude Code integration (this repo's
  own `AGENTS.md`, "Claude Code plugin surface" section) — running
  `openspec init --tools claude` per onboarded repo would generate a second,
  un-vetted copy that conflicts with it.
- `.worktrail/policy.yaml` is seeded with header comments only,
  no keys set — every key defaults to a safe, do-nothing value (see
  `router/policy.py`'s `DEFAULTS`). Don't guess a repo's `pre_pr_cmd` or
  `automerge` settings on its behalf; that's a follow-up decision for
  whoever owns the repo, not this skill.
- `worktrail-auto-merge.yml` is inert until something applies a
  `go:risk-low`/`go:risk-medium` label — a repo that never uses worktrail-go's
  classifier for its PRs never has it fire. But if `ci_jobs_discovered` came
  back empty (no CI, no `required_status_checks`), flag this explicitly to the
  user: a risk-labeled PR on that repo will merge with nothing gating it at
  all until real CI checks are added to `.github/rulesets/*.json`.
- `--with-aspens` reuses the existing `AspensAddOn` (`worktrail.addons.aspens`)
  directly for a one-time `install()`+`configure()` at bootstrap — it never
  calls `AddOn.run()` (`aspens doc sync`), since that's the per-task
  stage-and-commit path `worktrail.addons.runner` owns, not a bootstrap
  concern. A failed/unreachable `aspens` CLI surfaces as a warning, not a
  propose failure — `.aspens.json` existing is the only reliable success
  signal available (the add-on swallows subprocess errors as best-effort
  priming).
