# Research: openspec validate CI gate in repo-init

Source: work-queue brief `20260822-171811-worktrail-repo-init-when-openspec`.

## Problem

No repo onboarded via `worktrail-repo-init` validates its OpenSpec `specs/`/`changes/`
tree in CI. `openspec validate --all --strict` exists and works (confirmed against
datalena's `openspec/` dir: 39 items, all valid), but nothing runs it automatically.
datalena's `qa-pipeline.yml` treats `openspec/**` purely as a paths-filter
**exclusion** — an openspec-only diff skips app/api/web tests entirely, with no
replacement check of its own. A malformed change (bad `## ADDED/MODIFIED` delta
header, a requirement missing its `####`-scenario) currently merges silently; the
failure only surfaces later, by hand or when worktrail's own `compile.py`/orchestrator
chokes on it.

**Who benefits:** engineers/agents authoring OpenSpec changes in any repo `repo-init`
touches — fast red-CI feedback on a malformed change instead of silent drift.
**Smallest complete outcome:** `repo-init` scaffolds (a) a CI job that runs
`openspec validate --all --strict` on `openspec/**` diffs, paths-filtered so it's a
no-op elsewhere, and (b) makes that job's result actually gate merges on the
protected branch(es).
**What would make this commercially unsuccessful:** a required check with no
matching workflow (permanent PR deadlock — GitHub's classic failure mode for
`required_status_checks`), or a check that's just decorative (present but not
required, so it's ignored exactly like today).

## Prior art

`worktrail-overlap-check` against this repo's own `docs/specs/` + `openspec/` trees:
no overlapping spec. One keyword hit, `ci-bookkeeping-changes-gate` (complete) — but
that's worktrail's own CI classifying bookkeeping-only PRs to skip its own
`Lint, Test & Build`, unrelated to scaffolding a check into *other* repos via
repo-init.

## What's already there

`src/worktrail/onboarding/repo_init.py` already solves an almost-identical problem
for a different check — the auto-merge workflow:

- `AUTOMERGE_WORKFLOW_RELPATH = ".github/workflows/worktrail-auto-merge.yml"` +
  `build_automerge_workflow()` write a **self-contained, portable workflow file**
  into the target repo (not an injected step into the repo's own bespoke CI job) —
  because repo-init doesn't know the target repo's language/CI shape, only that it
  can drop in a standalone file. This is the template to copy for an
  `openspec-validate` job: a new `worktrail-openspec-validate.yml`, not an attempt to
  patch the repo's own `Lint, Test & Build` job (which repo-init doesn't own or
  understand well enough to safely edit).
- `discover_ci_checks()` already scans `.github/workflows/*.yml` job `name:` fields —
  a newly-scaffolded `openspec-validate` workflow would be picked up by this scan
  for free, with zero new discovery code.

## The real tension: `required_status_checks` is deliberately never auto-populated

`build_ruleset_for_branch()` calls `build_ruleset(..., required_status_checks=[])` —
always empty at scaffold time. The module docstring is explicit about why:

> `propose` deliberately never auto-populates `required_status_checks` — it reports
> discovered CI job display names ... for a human to review, since not every job
> should gate every branch ... A repo with no CI gets a ruleset with zero required
> checks, not a copy-pasted list that would deadlock every future PR.

The brief's ask — "wire it into required_status_checks" — runs directly against this
existing, deliberate design rule. This is the one real product decision here; it
isn't a coding detail.

## Candidate approaches

1. **Scaffold the workflow only, change nothing about the ruleset rule.**
   `discover_ci_checks()` picks up the new job automatically, so it appears in the
   existing informational report a human already reviews before running `apply`.
   Zero exception to the "never auto-required" rule. Weakest match to the brief's
   literal ask (not actually *required* until a human acts).
2. **Carve a narrow, deliberate exception for this one check.** When
   `init_openspec()` (or a sibling step on the same `openspec_initialized` gate)
   writes the new workflow, also append its job display name to
   `required_status_checks` for the branch(es) being protected — but *only* this
   specific, worktrail-authored check, never a general "auto-require everything
   discovered" policy. Defensible because worktrail authors the workflow file *and*
   the ruleset *and* the (empty, trivially-valid) `openspec/` scaffold in the same
   commit/PR, so — unlike an arbitrary pre-existing repo job — there's no risk of
   requiring a check that doesn't exist yet or is known-flaky. Directly satisfies the
   brief's ask, at the cost of a documented, scoped exception to the current rule.
3. **Do nothing to rulesets; document the manual step.** Simplest, but leaves the
   actual gating value un-delivered — the same gap approach 1 leaves, with no
   automation benefit either.

## Risks / unknowns

- **Deadlock risk if approach 2 is taken carelessly.** Only safe because worktrail
  controls both the workflow file and the ruleset write in the same operation, on a
  scaffold it knows is valid. Extending the exception to *any other* discovered job
  would reintroduce exactly the deadlock scenario the current rule protects against.
- **Migration path (existing openspec repos).** `init_openspec()` is a no-op once
  `openspec/config.yaml` exists (repos already onboarded, like `worktrail` itself).
  The brief's "sibling step gated on the same `openspec_initialized` condition"
  phrasing implies the new CI-job step should also run for repos where openspec was
  already initialized in a prior `repo-init` pass — not just brand-new bootstraps —
  otherwise every already-onboarded repo (this one included) stays permanently
  ungated. Needs its own idempotency check (workflow file already present → skip,
  matching the existing `automerge_workflow_exists` pattern), independent of
  `openspec_initialized`.
- **`--strict` and CLI drift.** `openspec validate --all --strict` behavior is pinned
  to whatever `@fission-ai/openspec@latest` resolves to at CI run time (repo-init
  already installs `@latest`, not a pinned version) — a future CLI release tightening
  `--strict` could turn previously-valid specs red with no repo change. Out of scope
  to solve here, but worth a one-line callout in the scaffolded workflow's comments.
- **`apply` vs `propose` boundary.** `propose` only writes ruleset *files*; they take
  effect only once a human runs `apply` (or the drift-guard workflow) against GitHub.
  Approach 2 changes what `propose` writes, not whether/when it's enforced live —
  consistent with the existing propose/apply split, not a new live-effect surface.

## Recommendation

Approach 2, scoped narrowly (only the workflow this same repo-init pass just wrote,
never a general auto-require-everything-discovered policy), plus explicit handling of
the already-onboarded-repo case via workflow-file-presence idempotency rather than
`openspec_initialized` alone. This is a product-scope decision (loosening a
documented, deliberate design rule) rather than a pure implementation detail, so it
should go through Route C (spec first) rather than straight to Route D — the delta
spec is the right place to record the narrowed exception's exact boundary so it
doesn't drift into "auto-require whatever CI discovers" over time.
