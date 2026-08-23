## 1. Vendor the drift-guard template content

- [x] 1.1 Add vendored `rulesets_sync.py` and `requirements.txt` content as string constants
      (mirroring devops's canonical `scripts/ci/rulesets/{rulesets_sync.py,requirements.txt}`),
      either inlined in `src/worktrail/onboarding/repo_init.py` next to `_AUTOMERGE_WORKFLOW` or
      in a sibling `src/worktrail/onboarding/rulesets_drift_guard_template.py` module if inlining
      makes `repo_init.py` unreadable.
- [x] 1.2 Add a `RULESETS_DRIFT_GUARD_WORKFLOW_RELPATH` constant
      (`.github/workflows/rulesets_drift_guard.yml`) and `RULESETS_SCRIPT_DIR_RELPATH`
      (`scripts/ci/rulesets`) constants, matching `AUTOMERGE_WORKFLOW_RELPATH`'s naming
      convention.

## 2. Branch-model-aware workflow generation

- [ ] 2.1 Add `build_rulesets_drift_guard_workflow(branches: List[str]) -> str` that renders the
      workflow YAML with `pull_request.branches` and `push.branches` set to the given branch
      list (`["dev", "prd"]` or `["dev", "stg", "prd"]`), the App-token mint step
      (`vars.RELEASE_NOTES_APP_ID` / `secrets.RELEASE_NOTES_APP_PRIVATE_KEY` via
      `actions/create-github-app-token`), and the credential-guard `if:` gating on the
      token-mint and rulesets-check/apply steps so they report `skipped` (not `failure`) when
      `vars.RELEASE_NOTES_APP_ID` is empty. Implements requirements: scaffolded workflow
      targets the repo's actual branch model; scaffolded workflow mints its token from the
      existing fleet-wide App; missing App credentials produce a clean skip, not a failure.
- [ ] 2.2 Add a final always-run step in the generated job that prints a one-line notice when the
      credential guard was false (App not configured), so a skipped run's logs explain why.

## 3. Wire scaffolding into `cmd_propose`

- [ ] 3.1 Extend `detect_state()` with `rulesets_drift_guard_exists` /
      `rulesets_sync_script_exists` checks (mirroring `automerge_workflow_exists`).
- [ ] 3.2 In `cmd_propose`, after the rulesets JSON loop (which already computes `branches`),
      write `scripts/ci/rulesets/{rulesets_sync.py,requirements.txt}` if absent, and write
      `.github/workflows/rulesets_drift_guard.yml` via
      `build_rulesets_drift_guard_workflow(branches)` if absent -- append to the same
      `written`/`skipped` lists `cmd_propose` already maintains. Implements requirement:
      propose scaffolds the drift-guard workflow and its vendored script.

## 4. Wire the credential reminder into `cmd_apply`

- [ ] 4.1 Add a helper (e.g. `app_credentials_configured(gh_repo) -> bool`) that checks for
      `RELEASE_NOTES_APP_ID` via `gh variable list --json name -R <gh_repo>` and
      `RELEASE_NOTES_APP_PRIVATE_KEY` via `gh secret list --json name -R <gh_repo>`, returning
      True only if both names are present.
- [ ] 4.2 In `cmd_apply`, after the existing rulesets apply loop, call the helper and append a
      one-line reminder (naming both the App-install step and the missing
      variable/secret) to `result["warnings"]` when it returns False. Implements requirement:
      apply reports a reminder when App credentials are missing.

## 5. Tests

- [ ] 5.1 In `tests/onboarding/test_repo_init.py`, add propose tests: fresh repo writes both the
      workflow and vendored script files; re-running propose skips an already-present
      `rulesets_drift_guard.yml`.
- [ ] 5.2 Add a test asserting `build_rulesets_drift_guard_workflow(["dev", "prd"])` and
      `build_rulesets_drift_guard_workflow(["dev", "stg", "prd"])` each produce the expected
      `branches:` lists, and that the generated YAML parses (`yaml.safe_load`) with the
      App-token step present and no step referencing `secrets.GITHUB_TOKEN` for the rulesets API
      call.
- [ ] 5.3 Add a test asserting the generated workflow's credential-guard `if:` conditions are
      present on the token-mint and rulesets-check/apply steps.
- [ ] 5.4 Add `cmd_apply` tests (mocked `gh` calls) covering: reminder printed when
      `RELEASE_NOTES_APP_ID`/`RELEASE_NOTES_APP_PRIVATE_KEY` are absent; no reminder when both
      are present.

## 6. Documentation

- [ ] 6.1 Update `skills/worktrail-repo-init/SKILL.md` to mention the newly scaffolded
      `rulesets_drift_guard.yml` + vendored sync script, and the apply-time App-credential
      reminder.
- [ ] 6.2 [cleanup] Confirm `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` both pass.
