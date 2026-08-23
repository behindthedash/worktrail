# Investigation: worktrail-repo-init doesn't scaffold a rulesets drift guard

Handoff brief: `20260822-172328-worktrail-repo-init-doesn-scaffold`. Route A.

## Problem framing

- **Problem:** `~/rules/CLAUDE.repo.md` section 3 lists a rulesets drift guard
  (verifies `.github/rulesets/*.json` matches GitHub's live ruleset state) as a
  "Recommended" default CI job for every repo, citing devops's
  `rulesets_drift_guard.yml` as the reference. `worktrail-repo-init` (this
  repo's onboarding tool) scaffolds the auto-merge workflow, rulesets JSON
  themselves, OpenSpec, and `.worktrail/policy.yaml`, but never scaffolds this
  job. Every repo onboarded via `repo-init propose`/`apply` therefore commits
  `.github/rulesets/*.json` with no CI check that GitHub's live state still
  matches it — an out-of-band GitHub UI edit (accidental or malicious) goes
  undetected indefinitely.
- **Who benefits:** the fleet operator (Brian) doing repo governance across
  `~/projects/`; the guard is what catches a ruleset edited outside the
  committed JSON before it silently diverges for weeks.
- **Smallest complete outcome:** `repo-init` either scaffolds a working drift
  guard for a newly onboarded repo, or makes a deliberate, documented decision
  to defer it with a clear reason — not a job that scaffolds cleanly but fails
  red on every fresh repo because its credentials were never wired up.
- **What would make this unsuccessful:** shipping a job that assumes the
  GitHub App is already installed on the target repo, so every freshly
  onboarded repo's CI shows a permanently-failing "Rulesets drift check" that
  operators learn to ignore (alert fatigue) — or copy-pasting the real
  `rulesets_sync.py` + `requirements.txt` into every fleet repo when a lighter
  answer already exists in practice (see below).

## Verified Observations

- `grep -rn "drift_guard\|drift-guard" src/worktrail/onboarding/repo_init.py`
  (this repo) returns nothing — confirmed absent from both `cmd_propose` and
  `cmd_apply`.
- `src/worktrail/onboarding/repo_init.py` has no App-token, `gh secret set`,
  or `gh variable set` call anywhere — the only credential referenced by
  anything it scaffolds is `secrets.GITHUB_TOKEN` (default token, used only in
  the generated auto-merge workflow). It cannot make a scaffolded drift-guard
  job pass day one even if the workflow file itself were copied in.
- **Not centralized today — every fleet repo carries its own full copy.**
  `devops`, `datalena`, and `gracefully-giving-back` (GGB) each have their own
  `rulesets_drift_guard.yml` *and* their own copy of `rulesets_sync.py` +
  `requirements.txt` (devops/datalena at `scripts/ci/rulesets/`, GGB at a
  different path, `ci/scripts/rulesets/`). None of the three workflow files
  declares `on: workflow_call`, so there is no existing reusable-workflow
  entry point to call from a fourth repo — `uses:
  behindthedash/devops/.github/workflows/rulesets_drift_guard.yml@main` is
  not possible against the current devops workflow as written.
- **One GitHub App already covers the fleet — confirmed, not assumed.** All
  three copies (devops, datalena, GGB) mint their CI token from the *same*
  App identity: `vars.RELEASE_NOTES_APP_ID` / `secrets.RELEASE_NOTES_APP_PRIVATE_KEY`
  via `actions/create-github-app-token`. This matches memory
  `reference_github_apps.md` — `behindthedash-automation`/`-release-notes`
  apps are already installed fleet-wide. So the App-identity question (open
  question 3 in the brief) is answered: no new App is needed, but the
  `RELEASE_NOTES_APP_ID` variable and `RELEASE_NOTES_APP_PRIVATE_KEY` secret
  still have to exist *in the specific target repo's* Actions settings for
  its own copy of the workflow to mint a token — that's a real per-repo
  onboarding gap, not a design question.
- **The App-install/credential gap is real and unaddressed.** GGB's own
  history confirms the failure mode this brief warns about: GGB's first
  attempt (`e7ddad0f`, "Add rulesets-as-code drift guard") tried the default
  `GITHUB_TOKEN` with an `administration` permission key, which
  `scripts/ci/rulesets/README.md` records as invalid (`administration` is not
  a valid `permissions:` scope) and caused CI runs to fail to even schedule a
  job — it took a follow-up fix (`18f024ac`) to switch to the App-token
  pattern. A freshly `repo-init`-onboarded repo without the App-token step
  pre-wired would reproduce exactly that failure.
- `rulesets_sync.py --check` requires network access to GitHub's rulesets API
  (`Administration:Read`, an App-only permission) — there is no credential-free
  or default-token path that works, per `scripts/ci/rulesets/README.md`.

## Unknowns / Missing Evidence

- Whether the App (`RELEASE_NOTES_APP_ID`) is *installed* (GitHub App
  installation, separate from the repo variable/secret existing) on every
  repo already onboarded via `repo-init` (e.g. `hearsay`, cited in the brief
  as currently exposed) — not verified in this pass; would need `gh api
  /repos/<owner>/<repo>/installation` or the GitHub App's install list, which
  needs org-level App-management access this session did not check.
- Whether GGB's `ci/scripts/rulesets/` path divergence from
  devops/datalena's `scripts/ci/rulesets/` was a deliberate repo convention
  or drift — not investigated; irrelevant to the recommendation below since
  neither path is being centralized.

## Candidate approaches

1. **Per-repo copy, `repo-init`-scaffolded.** `repo-init apply` writes its own
   `scripts/ci/rulesets/{rulesets_sync.py,requirements.txt,README.md}` (copied
   from devops's canonical version) plus the workflow file, exactly mirroring
   what datalena already does by hand. Matches the existing fleet pattern
   (3-of-3 current instances do this), zero new indirection, easy to audit
   per-repo. Cost: the sync script becomes a 4th (and future Nth) copy that
   only updates when someone remembers to re-copy it — no propagation
   mechanism today (unlike `worktrail-plugin-refresh.sh` for this repo's own
   skills).
2. **Reusable `workflow_call` from devops.** Add `on: workflow_call` to
   devops's `rulesets_drift_guard.yml`, keep `rulesets_sync.py` living only in
   devops, and have `repo-init` scaffold a thin `uses:
   behindthedash/devops/.github/workflows/rulesets_drift_guard.yml@main`
   caller in the target repo instead of copying the script. Fixes the
   propagation problem structurally (one place to fix bugs/add rulesets
   features), but is a **new pattern for this fleet** — no other reusable
   workflow currently spans repos this way (per `user_github_plan_no_cross_repo_gha.md`
   memory: cross-repo `workflow_call` needs the calling repo to be
   public, or the called workflow's repo used via PAT checkout / vendored
   copy — devops's visibility was not checked in this pass). Would also
   require devops to accept `.github/rulesets/**` and `secrets`/`vars` inputs
   parameterized per caller repo, which its current workflow does not do
   (hardcoded to its own `main`/`dev`/`stg`/`prd` branches).
3. **Scaffold conditionally, self-skip when uninstalled.** Regardless of (1)
   or (2), make the generated job probe for the App token first (e.g. a
   cheap `if: vars.RELEASE_NOTES_APP_ID != ''` guard, or a first step that
   attempts the token mint and marks the job `skipped` rather than `failed`
   on a missing/invalid App-token secret) so a freshly onboarded repo shows a
   clean skip instead of the red-X alert-fatigue failure mode the brief
   explicitly warns against.

## Recommendation

Approach 1 (per-repo copy) + approach 3 (clean-skip guard), not approach 2.
Reasoning: the fleet has zero precedent for cross-repo `workflow_call` today,
and standing up one correctly (visibility, parameterizing branches/paths,
proving it against a 4th consumer) is materially more design and testing work
than copying three already-proven files that have not needed to change since
GGB's last fix. The App-credential question that made this feel like it
needed real design work is already answered — one App, already fleet-wide —
so what's left is mechanical: `repo-init apply` should (a) copy
`scripts/ci/rulesets/{rulesets_sync.py,requirements.txt}` from a vendored
template in this repo (kept in sync with devops's canonical copy the same way
other repo-init templates are authored), (b) scaffold the workflow with the
same App-token pattern, and (c) either check for
`RELEASE_NOTES_APP_ID`/`RELEASE_NOTES_APP_PRIVATE_KEY` existing in the target
repo before enabling the schedule trigger, or ship the job with a
guard step that reports a clean skip (not a failure) when the App token mint
fails, with a one-line apply-time reminder to install the App and set the
repo variable/secret if they're missing. This does not require touching
devops or GGB.

Revisit approach 2 later if a 4th+ repo's copy drifts in a way that shows the
per-repo-copy cost is now real (the propagation problem this note flagged as
approach 1's cost), rather than deciding it up front on zero evidence of it
being a problem yet.

## Decision needed

Proceed to Route C (spec the `repo-init apply` change: vendor the sync script
template + workflow + credential-guard step) — feature-planning first, since
this does add new generated files and a new apply-time check, not a one-line
fix. Defer (leave as a handoff) is also reasonable if there's a reason to
sequence this behind the two other in-flight `repo_init.py` changes
(`20260822-164628` GitNexus registration, `20260822-172309` label creation)
landing first to reduce merge-conflict surface on the same file.
