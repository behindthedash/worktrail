# Branch model decision: 2 (dev/prd) vs 3 (dev/stg/prd)

`worktrail-repo-init` never guesses this — it's a `--branch-model` flag the
caller decides before running `propose`, because it changes the shape of
every generated `.github/rulesets/*.json` file (dev is squash-only with
`required_linear_history` in the 3-branch model, merge-commit-only without it
in the 2-branch model).

## Default to 2 (dev/prd)

A `stg` branch exists to gate promotion against a real, distinct
pre-production **deployed environment** — not as a generic "extra review
step." Without a real environment to test against, `stg` protects nothing
that `dev` doesn't already protect, and adds a promotion hop nobody can
meaningfully verify.

Use 2 when:
- The repo has no deployed backend/service at all (a CLI tool, a desktop app
  distributed via GitHub Releases, a library, workspace tooling).
- The repo deploys to exactly one live environment (a single Render service,
  a single Postgres-backed site) with no separate staging instance.
- The deploy platform already provides an equivalent per-PR preview gate
  (e.g. Vercel preview deployments) — a persistent `stg` branch would
  duplicate that without adding a real check.

## Use 3 (dev/stg/prd) only when a real staging environment exists

This is datalena's pattern: `dev`, `stg`, and `prd` each point at their own
live Render environment, and a CI-enforced `promotion-target-guard` check
rejects a non-canonical promotion pairing (a PR into `stg` must have `dev` as
its head; a PR into `prd` must have `stg` as its head). If you propose a
3-branch model, that promotion-pairing guard is NOT something
`worktrail-repo-init` generates for you — it's genuinely repo-specific CI
(datalena's `scripts/ci/check_promotion_target.py`), and a 3-branch repo
without it is missing the enforcement that makes the extra hop worth having.
Flag this explicitly to the user rather than silently shipping an
unenforced 3-branch model.

## Known drift, not a template to copy

Gracefully-giving-back uses `dev`/`main` (not `dev`/`prd`) — this is drift
from the doctrine's own naming (`~/rules/CLAUDE.repo.md` section 4 specifies
`prd`, not `main`/`production`/`prod`), not a second valid pattern. New repos
onboarded through this skill always get `prd`, never `main`, for the
production branch name.

## When it's genuinely unclear

Read the repo's README/AGENTS.md for a hosting/deployment section first. If
it's still ambiguous whether a distinct staging environment exists, ask the
user directly with `AskUserQuestion` — don't infer a 3-branch model from
"the repo seems important" or a 2-branch model from "no time to check."
