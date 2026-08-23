## Context

See `proposal.md` - Why. Relevant existing code in
`src/worktrail/onboarding/repo_init.py`:

- `discover_ci_checks()` scans `.github/workflows/*.yml` job `name:` fields — read-only,
  used only to populate an informational report today.
- `build_ruleset_for_branch()` always calls `build_ruleset(..., required_status_checks=[])`
  — empty at scaffold time, by deliberate design (see the module docstring).
- `AUTOMERGE_WORKFLOW_RELPATH` / `build_automerge_workflow()` / the write step keyed off
  `automerge_workflow_exists` is the direct precedent this change follows for the new
  workflow.
- `init_openspec()` is a no-op once `openspec/config.yaml` exists — it does not itself
  gate anything else in `propose`.

## Goals / Non-Goals

**Goals:**
- Reuse the exact scaffold-a-portable-workflow-file pattern already proven by
  `worktrail-auto-merge.yml`.
- Keep the `required_status_checks` exception mechanically narrow: it must be
  structurally impossible for this change to cause any *other* discovered job to become
  auto-required.
- Work correctly for both a brand-new repo and a repo that was onboarded before this
  change shipped.

**Non-Goals:**
- Not retrofitting a full `repo-init` capability spec — this change specs only the new
  CI-gate behavior (see proposal's Capabilities section).
- Not changing `apply`'s behavior — it still only live-applies whatever ruleset JSON
  `propose` already wrote; this change only affects what `propose` writes.
- Not adding a general "auto-require every discovered job" policy — explicitly rejected
  in the research note (`docs/specs/research/openspec-validate-ci-gate.md`) as
  reintroducing the deadlock risk `required_status_checks` staying empty-by-default
  protects against.

## Decisions

**Standalone workflow file, not an injected step.** Mirrors `worktrail-auto-merge.yml`:
`repo-init` doesn't know the target repo's language or existing CI job structure, so it
writes a self-contained file it fully owns rather than attempting to parse and patch an
arbitrary existing workflow. Alternative considered (injecting a step into the repo's
own `Lint, Test & Build` job, per the workspace's job-table convention) was rejected —
that convention governs jobs a *repo* authors for itself, not files `repo-init`
generates and owns across many repos with no shared shape.

**Exception keyed on "newly written this run", not "workflow file exists".** The
`required_status_checks` append only fires in the same `propose` invocation that writes
the workflow file for the first time — never on a later `propose` run where the file
already exists. This keeps the causality tight: `propose` only ever requires a check it
is certain, in the same operation, is backed by a real, freshly-written workflow file
landing in the same commit. A later idempotent no-op run touches neither the workflow
nor the ruleset. Alternative considered: re-assert the required-check entry on every
`propose` run regardless of whether the workflow was newly written — rejected because
it reopens the door to "propose silently rewrites ruleset JSON a human may have
hand-edited," which the current design avoids entirely by writing
`required_status_checks: []` once and never touching it again.

**Patch existing ruleset files in place; never regenerate them.** `cmd_propose`'s
ruleset loop today is `if path.is_file(): skip; continue` — it never touches a
`protect-<branch>.json` that already exists (an already-onboarded repo's normal state).
For that case, this change adds a narrow, separate patch step, gated on the same
"workflow newly written this run" predicate: read the existing JSON, locate its
`required_status_checks` rule (creating an empty one if the file predates any required
checks), append `{"context": <job name>}` only if not already present, and write the
file back unchanged otherwise. Alternative considered: fold the openspec-validate check
into `build_ruleset_for_branch()` and simply overwrite the existing file — rejected,
since that function assumes it's building a ruleset from scratch and would silently
discard any other required checks a human has since added by hand to that same file
between `repo-init` runs.

**Idempotency keyed on workflow-file presence, not `openspec_initialized`.** Using
`openspec_initialized` alone would permanently skip already-onboarded repos (like
`worktrail` itself), since that flag is already `True` for them before this change ever
ships. Checking the workflow file's own presence (mirroring
`automerge_workflow_exists`) means a repo onboarded before this change gets caught up
on its next `repo-init propose` pass, exactly like the automerge workflow's own rollout
did.

## Risks / Trade-offs

- **[Risk] `openspec validate --all --strict` behavior drifts with `@fission-ai/openspec@latest`** (unpinned CLI version) → **Mitigation**: out of scope for this change (repo-init already installs `@latest` elsewhere); the scaffolded workflow's own comments note the unpinned-version tradeoff so a future repo can pin it if it becomes a problem.
- **[Risk] A protected branch already at its `apply`-applied ruleset gets a new required check added by `propose` but never re-`apply`'d** → **Mitigation**: unchanged from today's `propose`/`apply` split — `propose` only ever writes files; a human (or the existing drift-guard workflow) is responsible for running `apply` to make it live, exactly as with every other ruleset change `propose` produces.
- **[Risk] Scope creep — a future change generalizes this exception into "auto-require anything discovered"** → **Mitigation**: the spec's "Other CI jobs remain unaffected" scenario and this design's Non-Goals exist specifically to make that an explicit, reviewable spec change rather than a silent extension of this one.

## Migration Plan

No migration needed — this only changes what future `propose` runs write. Existing
repos are unaffected until their next `repo-init propose` pass, at which point they
pick up the new workflow (and, since it's newly written for them at that point, the
paired `required_status_checks` entry) the same way any other repo does.
