## Context

See `proposal.md` - Why for motivation and `docs/specs/research/rulesets-drift-guard-not-scaffolded-by-repo-init.md`
for the underlying investigation. Relevant current state:

- `repo_init.py` already has a working pattern for this exact shape of problem:
  `AUTOMERGE_WORKFLOW_RELPATH` + `_AUTOMERGE_WORKFLOW` (an inlined string template) +
  `build_automerge_workflow()`, written in `cmd_propose` with a write-if-absent check against
  `state["automerge_workflow_exists"]`. The drift-guard scaffolding follows this same shape.
- `cmd_propose` already computes `branches` (`["dev", "prd"]` or `["dev", "stg", "prd"]`) from
  `args.branch_model` before writing the rulesets JSON -- the same list is what the workflow's
  triggers need, so no new branch-model logic is required, only reuse.
- devops's `rulesets_drift_guard.yml` + `scripts/ci/rulesets/{rulesets_sync.py,requirements.txt}`
  is the canonical reference (confirmed in the discovery note as logic-identical to datalena's
  and GGB's copies, differing only in `User-Agent` string and docstring incident references, and
  in which branches the workflow triggers on).
- `cmd_apply` already prints a "Manual follow-up" block of human next-steps after its GitHub API
  calls (retarget open PRs, `git fetch && git switch dev`) -- the credential reminder fits the
  same place, driven by the same `result`/warnings structure `cmd_apply` already builds.

## Goals / Non-Goals

**Goals:**
- Every `propose`-onboarded repo ends up with a working drift-guard job once the App is
  installed and configured, with no additional design work beyond copying an already-proven
  file set.
- A freshly onboarded repo, before anyone installs the App on it, shows a clean skip in CI, not
  a red X -- avoiding the exact alert-fatigue failure mode GGB hit first.
- `apply` surfaces the one missing manual step (installing the App / setting the repo
  variable+secret) instead of leaving it undiscoverable until someone notices the workflow
  skipping forever.

**Non-Goals:**
- No new cross-repo `workflow_call` / reusable-workflow pattern. The discovery note found zero
  existing precedent for one in this fleet and judged standing one up (visibility, parameterized
  branches/paths, a fourth consumer to prove it against) not worth the cost right now. Revisit
  only if a future 4th+ vendored copy actually drifts in a way that shows per-repo-copy has a
  real, not hypothetical, cost.
- No changes to devops, datalena, or GGB's existing copies -- they remain the reference; this
  change reads from them (as a one-time vendoring at authorship time) but does not establish an
  ongoing sync mechanism back to them.
- No automated GitHub App installation. Installing `behindthedash-automation`/`-release-notes`
  on a new repo is an org-level action outside what a `propose`/`apply` CLI running from a local
  checkout (or `gh` as a repo-scoped actor) can safely do unattended; this stays a documented
  manual step surfaced by `apply`'s reminder.

## Decisions

**Where the vendored template content lives.** Store the vendored `rulesets_sync.py` and
`requirements.txt` content as Python string constants in `repo_init.py` (or a small sibling
module, e.g. `onboarding/rulesets_drift_guard_template.py`, if inlining bloats `repo_init.py`
past a readable size), mirroring `_AUTOMERGE_WORKFLOW`'s pattern exactly, rather than reading
from a checked-in template file under a `templates/` directory.
- *Alternative considered:* ship the template as a real `.py`/`.txt` file under a `templates/`
  directory and read it with `importlib.resources` at scaffold time. Rejected for this change --
  `_AUTOMERGE_WORKFLOW` already establishes the inlined-string convention for exactly one
  generated workflow file, and introducing a second packaging mechanism (resource loading) for
  only this addition is inconsistent with that existing precedent without a concrete reason
  (e.g. multi-file template growth) to justify it now.

**Branch-model-aware trigger generation.** Build the workflow string with the same `branches`
list (`["dev", "prd"]` / `["dev", "stg", "prd"]`) `cmd_propose` already computes, via simple
string formatting (`", ".join(branches)` into the YAML `branches: [...]` lines), the same way
`build_ruleset_for_branch` already branches on `args.branch_model`.
- *Alternative considered:* hardcode `main` like devops's own copy. Rejected -- devops is not
  itself `repo-init`-onboarded (it predates this tool and never renamed its default branch), so
  copying its trigger verbatim would scaffold a workflow that never fires on a `repo-init`
  target repo, which always ends up on `dev`/`stg`/`prd`.

**Credential-guard step shape.** Add a first step to the job that checks
`vars.RELEASE_NOTES_APP_ID != ''` (a job-level or step-level `if:`, matching GitHub Actions'
supported syntax for gating on a `vars` context value) and gates the token-mint and
rulesets-check/apply steps behind it, plus a final always-run step that prints a one-line
"App not configured, skipping" notice when the guard is false. This keeps the job's overall
conclusion `success` (steps report `skipped`, not `failure`) instead of using a separate
`continue-on-error: true` on the API-calling steps, which would report `success` with a
misleading green check even though nothing was actually verified.
- *Alternative considered:* `continue-on-error: true` on the rulesets-check step. Rejected --
  it produces a passing job when the check never ran, which is worse than an honest skip; a
  human scanning green checks would have no signal that rulesets drift is unverified.

**`apply`'s credential check.** Use `gh variable list --json name` and `gh secret list --json
name` (both readable without extra permissions beyond what `apply` already needs for its other
`gh api` calls) to check for `RELEASE_NOTES_APP_ID` / `RELEASE_NOTES_APP_PRIVATE_KEY` by name
existing in the target repo, without attempting to read secret values (impossible via the API,
and unnecessary -- existence is the only thing that matters here). Append the reminder to the
same `result["warnings"]` list `cmd_apply` already prints, rather than introducing a second
output channel.
- *Alternative considered:* attempt to mint an App token from `apply` itself as the check (an
  actual functional test, not just a name-existence check). Rejected as unnecessary complexity
  -- `apply` runs from a local checkout with `gh auth token`-scoped credentials, not the App's
  private key, so it cannot mint an App token anyway; checking that the *repo-level* variable
  and secret exist is the correct-altitude check for what `apply` can actually verify from
  outside CI.

## Risks / Trade-offs

- **Vendored copy drifts from devops's canonical version over time.** → Accepted per the
  proposal's non-goal on `workflow_call`; the discovery note explicitly weighed this against
  building cross-repo infra now and recommended deferring until a real drift incident shows the
  cost. No mitigation beyond noting in code comments (matching devops's own README) that this is
  vendored and should be kept logic-identical when devops's copy changes.
- **A repo onboarded before the App is installed shows a permanently-skipped job until someone
  acts on the reminder.** → Mitigated by `apply`'s printed reminder being impossible to miss in
  the same output block operators already read for other manual follow-ups; not eliminated, since
  installing the App remains an intentionally manual, org-level step (see Non-Goals).
- **`gh variable list`/`gh secret list` calls in `apply` require the invoking `gh` session to
  have at least read access to the target repo's Actions settings.** → Same trust boundary
  `apply` already operates under for its existing `gh api` branch/ruleset calls; no new
  permission surface introduced.
