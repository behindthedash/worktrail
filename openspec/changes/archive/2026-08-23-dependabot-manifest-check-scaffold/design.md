## Context

See `proposal.md` — Why. The relevant existing state:

- `src/worktrail/onboarding/repo_init.py` (~1080 lines) owns all file generation for
  `worktrail-repo-init propose`. Every scaffolded artifact follows one shape: a
  `*_RELPATH` module constant, a `detect_state()` key recording whether it already exists, and a
  write-if-absent block in `cmd_propose()` appending to `written` or `skipped`.
- `src/worktrail/onboarding/rulesets_drift_guard_template.py` holds the vendored
  `rulesets_sync.py` and its `requirements.txt` as module-level *string constants*, explicitly so
  the CLI has no runtime dependency on package-data resolution. `repo_init.py` imports them and
  writes them verbatim.
- Workflow YAML is built by a function (`build_rulesets_drift_guard_workflow(branches)`) when it
  needs branch-model substitution, or returned from a flat literal
  (`build_openspec_validate_workflow()`) when it does not.
- `repo-init-ci-gate` (an existing capability) states `propose` SHALL NOT add any CI job other
  than `openspec-validate` to `required_status_checks`.
- Existing tests: `tests/onboarding/test_repo_init.py` parses generated workflow YAML and asserts
  on its structure; `tests/test_rulesets_sync.py` loads worktrail's *own vendored copy* of
  `scripts/ci/rulesets/rulesets_sync.py` via `importlib.util.spec_from_file_location`.

The upstream logic being generalized is `behindthedash/devops`
`scripts/test_dependabot_config.py` (PR #306): a `unittest.TestCase` hardcoded to that repo's
root, with a two-entry `_ECOSYSTEM_MANIFEST_GLOBS` dict and a `_manifest_exists()` helper using
non-recursive `Path.glob`.

## Goals / Non-Goals

**Goals:**

- Structural symmetry with `rulesets-drift-guard-scaffold`, so a reader who knows one knows both:
  template module of string constants → `repo_init.py` relpaths + builder + state keys → three
  write-if-absent blocks → tests.
- The vendored script is a standalone CLI (`python scripts/ci/dependabot/dependabot_manifest_check.py`),
  not a `unittest.TestCase` — it must run identically from a workflow step, a pre-commit hook, or a
  human's shell in any repo, none of which should need a test runner.
- Never produce a false failure. An unrecognized ecosystem, a glob-patterned `directories` entry,
  or an absent config is a skip/pass, not a red check.

**Non-Goals:**

- Reproducing Dependabot's real manifest-discovery semantics (lockfile-only directories, vendored
  workspaces, ecosystem-specific fallbacks). This check is a coarse "is there anything here at
  all" guard, matching the failure it is designed to catch.
- Validating anything else in `dependabot.yml` (schedule syntax, `open-pull-requests-limit`,
  registries, groups). Out of scope; the file name and the capability name are both about the
  directory/manifest relationship.

## Decisions

### D1 — Vendored standalone script, not a devkit-style `unittest.TestCase`

devops's version is a `TestCase` because it lives next to a repo whose CI already runs
`python3 scripts/test_dependabot_config.py`. Scaffolding a `TestCase` into an arbitrary repo
couples the check to that repo having (and running) a test harness, and to `unittest`'s
discovery rules. A plain `argparse` script with `--repo` (default: the directory two levels above
the script, i.e. the repo root) and an explicit exit code is portable to every target repo.

*Alternative considered:* ship the check as a `worktrail-*` console script and have the workflow
`pip install worktrail`. Rejected — it makes every onboarded repo's CI depend on worktrail being
installable and version-pinned, which is exactly the coupling the vendoring precedent avoids.
The rulesets guard already made this call.

### D2 — Ecosystem table stays at `pip` + `npm`

The table is the allowlist of what the check knows how to verify, and everything outside it is
skipped. Adding speculative rows (`cargo`, `gomod`, `bundler`, …) is untested surface that can
only introduce false failures, and adding one later is a one-line table edit in the template
constant. The table is defined once at the top of the vendored script with a comment stating that
adding a row is the extension point.

### D3 — Handle `directories:` (plural), skip glob values

Dependabot v2 accepts `directories: [...]` as well as `directory: "..."`. devops's version only
reads `directory`, so an entry using the plural form would be silently unchecked — a false pass in
exactly the configuration (monorepo, many manifests) most likely to have a mismatch. Handling it
is a few lines. Values containing `*`, `?`, or `[` are skipped rather than resolved: Dependabot
expands those against its own rules, and treating `/apps/*` as a literal path would fail every
repo that uses the feature correctly.

### D4 — No `paths:` filter on the workflow trigger

The rulesets drift guard is paths-filtered to `.github/rulesets/**` because that is the only
input it reads. This check's inputs are `dependabot.yml` *and every manifest file in the repo*.
A PR that deletes `tools/requirements.txt` breaks the config without touching `.github/`. The
alternatives were (a) enumerate the manifest globs in a `paths:` filter — duplicates the
ecosystem table in YAML where no test can keep the two in sync, and the duplication silently rots
the moment D2's table grows; or (b) add a weekly `schedule:` as the drift guard does — catches it
eventually but not at review time, which is the whole point of the change. Running unconditionally
costs a ~10-second `ubuntu-latest` job per PR. Taken.

### D5 — `pyyaml` via a vendored `requirements.txt`, mirroring the rulesets pair

`dependabot.yml` is YAML; hand-parsing it is not worth avoiding one dependency. The vendored
`scripts/ci/dependabot/requirements.txt` declares `pyyaml` and the workflow installs it, exactly
as `scripts/ci/rulesets/requirements.txt` declares `requests`. Two sibling `scripts/ci/<topic>/`
directories each with their own pinned requirements is the shape already established; a shared
`scripts/ci/requirements.txt` would couple the two scaffolds' install steps and break the
"either can be scaffolded independently" property.

### D6 — Tests exec the template constant, not a worktree-vendored copy

`tests/test_rulesets_sync.py` loads worktrail's own `scripts/ci/rulesets/rulesets_sync.py`, which
means the template constant in `rulesets_drift_guard_template.py` is itself untested and can drift
from the vendored copy unnoticed. Rather than repeat that, the new
`tests/onboarding/test_dependabot_manifest_check.py` writes
`DEPENDABOT_MANIFEST_CHECK_PY` to a `tmp_path` file, loads it with
`importlib.util.spec_from_file_location`, and drives it against synthetic repo trees. This tests
the artifact that actually ships, and keeps the change from depending on worktrail vendoring its
own copy (which is rollout work — see proposal, "Explicitly not in this change").

### D7 — Job display name

`Dependabot manifest check`, set as the job-level `name:`. Distinct from the workflow's
`name: "CI: Dependabot Manifest Check"`, following the doctrine rule that
`required_status_checks` entries use job display names — even though this change deliberately
does not add it as a required check (see spec), so a future decision to promote it has a stable
name to reference.

## Risks / Trade-offs

- **A false failure blocks a PR in someone else's repo.** → Every unknown input is a skip: unmapped
  ecosystem, glob directory value, absent config, empty `updates`. The only non-zero exits are
  "mapped ecosystem, literal directory, nothing matching there" and "config is not parseable YAML".
- **False pass on ecosystems Dependabot handles more subtly than "any matching filename".** →
  Accepted, and stated as a non-goal. The check is strictly better than the current state (no
  signal at all) and never claims to be a Dependabot emulator.
- **A per-PR job on every onboarded repo (D4) adds CI minutes.** → `ubuntu-latest`, checkout +
  `pip install pyyaml` + a filesystem walk; seconds, and none of it on a self-hosted runner.
- **The vendored copy in each onboarded repo can drift from worktrail's constant.** → Same
  known limitation the rulesets guard already carries; `propose` is write-if-absent by design and
  deliberately never overwrites a repo's hand-customized copy. Not solved here.
- **The scaffolded check is a no-op in a repo with no `dependabot.yml`.** → Real, and named in
  the proposal as follow-up (scaffolding `dependabot.yml` itself). Scaffolding the check first is
  still ordered correctly: the guard exists before the thing it guards.
