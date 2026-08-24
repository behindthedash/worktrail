## 1. Vendored check script template

- [x] 1.1 Create `src/worktrail/onboarding/dependabot_manifest_check_template.py` with a
  module docstring mirroring `rulesets_drift_guard_template.py`'s: what the vendored artifact
  guards against, why it is an inline string constant rather than package data, and that
  `behindthedash/devops` `scripts/test_dependabot_config.py` (PR #306) is the origin with no
  automated sync back.
- [x] 1.2 In that module, define `DEPENDABOT_MANIFEST_CHECK_PY` — the full standalone script:
  shebang, docstring explaining the silent Dependabot-Updates failure it catches, `argparse`
  with `--repo` (default: the repo root inferred from the script's own location) and
  `--config` (default: `<repo>/.github/dependabot.yml`), and `sys.exit(main())`.
- [x] 1.3 In the script body, define the ecosystem-to-manifest-glob table as a single top-level
  dict — `pip` → `("requirements*.txt", "setup.py", "pyproject.toml", "Pipfile")`, `npm` →
  `("package.json",)` — with a comment stating that adding a row is the extension point and that
  ecosystems absent from the table are skipped, never failed (design D2). Implements
  requirement: unrecognized ecosystems are skipped, never failed.
- [x] 1.4 Implement directory resolution and non-recursive manifest matching: `/` (and an omitted
  key) means the repo root, other values are repo-root-relative, and matching uses
  `Path.glob` directly in that directory with no recursion. Implements requirement: every
  checkable ecosystem entry must have a manifest under its directory.
- [x] 1.5 Implement `updates`-entry iteration covering both `directory` (string) and
  `directories` (list); skip any directory value containing `*`, `?`, or `[` (design D3); skip
  entries whose `package-ecosystem` is not in the table. Implements requirement: multi-directory
  entries are checked per directory, with glob patterns skipped.
- [x] 1.6 Implement exit-code and output behavior: zero with a "nothing to check" message when
  the config is absent or declares no `updates`; zero with an "in sync"-style summary when every
  checkable entry resolves; non-zero naming each offending `ecosystem` + `directory` pair on
  stderr; non-zero naming the parse error when the YAML is malformed. Implements requirement:
  absent or empty Dependabot config is a clean pass.
- [x] 1.7 In the same module, define `DEPENDABOT_MANIFEST_CHECK_REQUIREMENTS_TXT` declaring
  `pyyaml` (design D5).

## 2. repo_init wiring

- [x] 2.1 In `src/worktrail/onboarding/repo_init.py`, add a "Dependabot manifest check" constants
  block next to the rulesets one: `DEPENDABOT_CHECK_WORKFLOW_RELPATH`
  (`.github/workflows/dependabot_manifest_check.yml`), `DEPENDABOT_CHECK_SCRIPT_DIR_RELPATH`
  (`scripts/ci/dependabot`), `DEPENDABOT_CHECK_SCRIPT_RELPATH`,
  `DEPENDABOT_CHECK_REQUIREMENTS_RELPATH`, and `DEPENDABOT_CHECK_JOB_NAME`
  (`Dependabot manifest check`, design D7). Import the two template constants.
- [x] 2.2 Add `build_dependabot_manifest_check_workflow(branches: List[str]) -> str`, modelled on
  `build_rulesets_drift_guard_workflow`: `name: "CI: Dependabot Manifest Check"`, a
  `pull_request` trigger with `branches:` substituted from the branch model and **no** `paths`
  key (design D4), `workflow_dispatch`, a `concurrency` group with `cancel-in-progress: true`,
  `permissions: contents: read`, and steps checkout → setup-python → install requirements → run
  the vendored script. Docstring must state why there is no paths filter and why no token is
  minted. Implements requirements: scaffolded workflow targets the repo's actual branch model;
  the check runs on every pull request, not only on dependabot.yml diffs; the check requires no
  GitHub credentials and makes no API calls.
- [x] 2.3 Add `dependabot_manifest_check_workflow_exists` and
  `dependabot_manifest_check_script_exists` keys to `detect_state()`.
- [x] 2.4 In `cmd_propose()`, add three write-if-absent blocks (script, requirements, workflow)
  following the rulesets blocks' exact shape — `mkdir(parents=True, exist_ok=True)`, append to
  `written` or to `skipped` with the `(already exists)` suffix. Place them after the rulesets
  drift-guard blocks. Do **not** touch `required_status_checks` or `required_check_configured`.
  Implements requirements: propose scaffolds the manifest check workflow and its vendored
  script; the scaffolded check is not wired into required_status_checks.

## 3. Tests

- [x] 3.1 Add `tests/onboarding/test_dependabot_manifest_check.py` that writes
  `DEPENDABOT_MANIFEST_CHECK_PY` to a tmp file, loads it via
  `importlib.util.spec_from_file_location` (design D6), and provides a helper that builds a
  synthetic repo tree (`.github/dependabot.yml` + arbitrary manifest files) under `tmp_path`.
- [x] 3.2 Cover the passing paths: `pip` at `/` with `pyproject.toml`; `pip` at `/` with only
  `requirements-dev.txt` (glob match); `npm` at a subdirectory with `package.json`; several
  entries all valid.
- [x] 3.3 Cover the failing paths: `pip` directory with no matching manifest; a `directory` that
  does not exist at all; `npm` at `/` where the only `package.json` is nested one level down;
  three entries with exactly one broken (assert the message names only the broken one).
- [x] 3.4 Cover the skip paths: `github-actions` entry; an unmapped ecosystem (`cargo`); an entry
  with no `directory` key defaulting to the root.
- [x] 3.5 Cover `directories` (plural): a list with one valid and one broken directory (exit
  non-zero, names the broken one), and a glob value `/apps/*` (skipped, exit zero).
- [x] 3.6 Cover the config-level cases: no `.github/dependabot.yml` at all → exit zero; config
  with a missing/empty `updates` list → exit zero; malformed YAML → exit non-zero naming the
  parse failure.
- [x] 3.7 Add workflow-shape tests to `tests/onboarding/test_repo_init.py` alongside the existing
  `build_rulesets_drift_guard_workflow` tests: parse the generated YAML and assert
  `pull_request.branches` is `[dev, prd]` for the 2-branch model and `[dev, stg, prd]` for the
  3-branch model, that the `pull_request` trigger declares no `paths`/`paths-ignore`, that no
  step references `secrets.` or `vars.`, and that a step runs the vendored script path.
- [x] 3.8 Add `cmd_propose` integration tests to `tests/onboarding/test_repo_init.py`: fresh repo
  writes all three files (including when the repo has no `.github/dependabot.yml`); a re-run
  leaves a hand-customized workflow byte-identical and reports it skipped; an existing vendored
  script with a missing workflow writes only the workflow; `propose --check` reports both new
  state keys without writing.
- [x] 3.9 Add a regression test asserting `propose` does not add `DEPENDABOT_CHECK_JOB_NAME` to
  `required_status_checks` in either a freshly generated `protect-*.json` or a pre-existing one.

## 4. Documentation and release

- [x] 4.1 Update `skills/worktrail-repo-init/SKILL.md`: add the workflow and its vendored script
  pair to the Overview list, and a short Step 2 paragraph next to the drift-guard one stating
  what the check does and that it needs no credentials at `apply` time.
- [x] 4.2 [cleanup] Run `pytest -q` and `python3 -m worktrail.orchestrator.orchestrate check`;
  both green.
- [x] 4.3 Bump `version` in `pyproject.toml` and `.codex-plugin/plugin.json` together, or apply
  the `go:no-version-bump` label if this lands in a deferred batch bump —
  `CI: Version Bump Check` fails otherwise because `src/worktrail/**` changed.
