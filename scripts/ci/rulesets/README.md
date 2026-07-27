# Worktrail rulesets drift guard

`.github/rulesets/*.json` (`protect-main`) is the
source of truth for branch protection, but **GitHub does not auto-apply edits
to those files.** Merging a change to a ruleset JSON only updates the committed
copy — someone still has to push it to the live ruleset via the API. Worktrail
has hit this gap before: the `Lint, Test & Build` required check had to be
applied to the live rulesets by hand after the JSON merged.

`rulesets_sync.py` closes that gap.

## Commands

```bash
# Fail if any committed ruleset differs from what's live on GitHub:
python scripts/ci/rulesets/rulesets_sync.py --check

# Push committed rulesets over the live ones:
python scripts/ci/rulesets/rulesets_sync.py --apply
```

Both default to `$GITHUB_REPOSITORY`/`$GITHUB_TOKEN` (set automatically in
Actions) or `git remote get-url origin`/`gh auth token` locally. Override with
`--repo owner/repo` / `--token <token>` if needed.

**CI token note:** reading live rulesets requires repository
Administration:Read, which is a GitHub App installation permission — it
cannot be granted to the default `GITHUB_TOKEN` via a workflow's
`permissions:` block (`administration` is not a valid key there; confirmed by
actionlint). `rulesets_drift_guard.yml` here mints a token from the App already
installed for bot automation (`vars.RELEASE_NOTES_APP_ID` /
`secrets.RELEASE_NOTES_APP_PRIVATE_KEY`, via `actions/create-github-app-token`)
instead.

## When to run `--apply`

**After every merge that changes a file under `.github/rulesets/`, someone
must run `--apply` manually** — merging the PR alone does not update GitHub's
live branch protection. `--apply` is deliberately **not** run automatically
post-merge: applying a tightened ruleset could immediately block the very
push/PR that's applying it (e.g. a new required check that hasn't reported
yet), so it stays a manual step until that chicken-and-egg case is handled.

CI (`.github/workflows/rulesets_drift_guard.yml`) runs `--check` on every PR
that touches `.github/rulesets/**` and fails the build on drift — that's the
signal that a `--apply` is owed, not something CI performs for you.

## Cross-repo

Vendored from the shared implementation in datalena (`scripts/ci/rulesets/`),
following the release-notes vendoring precedent. Keep
`rulesets_sync.py` logic-identical across repos when making engine changes
(only the `User-Agent` string and docstring incident references differ).
Unit tests live in this repo's root suite at `tests/test_rulesets_sync.py`.
