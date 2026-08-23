## Why

No repo onboarded via `worktrail-repo-init` validates its OpenSpec `specs/`/`changes/`
tree in CI. `openspec validate --all --strict` exists and works, but nothing runs it
automatically — a malformed change (bad delta header, a requirement missing its
scenario) currently merges silently, surfacing only later by hand or when worktrail's
own `compile.py`/orchestrator chokes on it. See
`docs/specs/research/openspec-validate-ci-gate.md` (PR #631) for the full discovery
note, including the three candidate approaches considered.

## What Changes

- `worktrail-repo-init propose` scaffolds a new, self-contained CI workflow,
  `.github/workflows/worktrail-openspec-validate.yml`, that runs
  `openspec validate --all --strict` on any diff touching `openspec/**` (paths-filtered
  so it's a no-op elsewhere) — mirroring the existing `worktrail-auto-merge.yml`
  scaffold pattern (a portable file `repo-init` writes and owns, not an edit to the
  target repo's own bespoke CI job).
- **BREAKING** (scoped, deliberate exception): when this workflow is newly written by
  the current `propose` run, its job display name is appended to
  `required_status_checks` for the branch(es) `build_ruleset_for_branch()` protects.
  This is the one narrowly-scoped exception to `propose`'s existing rule that it never
  auto-populates `required_status_checks` — every other discovered CI job is still only
  ever reported for human review, never auto-required. The exception is safe here
  specifically because `repo-init` authors both the workflow file and the ruleset entry
  in the same `propose` run, against a freshly-scaffolded (or already-valid, for
  already-onboarded repos) `openspec/` tree it knows will pass.
- Scaffolding this workflow (and the paired ruleset entry) is idempotent on the
  workflow file's own presence, not solely on `openspec_initialized` — so a repo that
  was onboarded before this change shipped (openspec already initialized, workflow
  absent) still gets both on its next `repo-init propose` pass, matching the existing
  `automerge_workflow_exists` idempotency pattern.

## Capabilities

### New Capabilities
- `repo-init-ci-gate`: scaffolding and required-status-check wiring for the
  OpenSpec-validation CI job that `worktrail-repo-init` writes into onboarded repos.

### Modified Capabilities
(none — `repo-init`'s broader propose/apply behavior has no existing capability spec to
delta against; this proposal scopes only the new CI-gate behavior into its own
capability rather than retroactively specifying all of `repo-init`.)

## Impact

- `src/worktrail/onboarding/repo_init.py`: new `OPENSPEC_VALIDATE_WORKFLOW_RELPATH` +
  `build_openspec_validate_workflow()` (mirrors `AUTOMERGE_WORKFLOW_RELPATH` /
  `build_automerge_workflow()`); a new write step alongside `init_openspec()`, gated on
  workflow-file-presence; `detect_state()` gains
  `openspec_validate_workflow_exists` (mirrors `automerge_workflow_exists`) so the
  "newly written this run" predicate is known before the ruleset loop runs.
  `build_ruleset_for_branch()` gains a `required_status_checks` entry for the new job's
  display name when generating a *fresh* ruleset file. For a branch whose
  `protect-<branch>.json` already exists (an already-onboarded repo, where today's loop
  skips the file entirely), `propose` gains a small in-place JSON patch step that adds
  the `required_status_checks` entry (creating the rule if absent) without touching the
  rest of the file — the existing "already exists → skip" loop does not, and must not,
  become a full regenerate.
- `propose`'s written/skipped report (`state["ci_jobs_discovered"]` etc.) gains the new
  workflow's entry, consistent with existing `discover_ci_checks()` behavior.
- No change to `apply` — it still only live-applies whatever `propose` already wrote to
  `.github/rulesets/*.json`.
- Test coverage: `tests/` mirrors `src/worktrail/onboarding/` — extend the existing
  `repo_init` test module for the new workflow scaffold, the ruleset
  `required_status_checks` entry, and the already-onboarded-repo idempotency case.
