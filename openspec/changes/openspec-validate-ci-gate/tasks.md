## 1. Workflow scaffold

- [ ] 1.1 Add `OPENSPEC_VALIDATE_WORKFLOW_RELPATH = ".github/workflows/worktrail-openspec-validate.yml"` and `build_openspec_validate_workflow()` to `src/worktrail/onboarding/repo_init.py`, mirroring `AUTOMERGE_WORKFLOW_RELPATH`/`build_automerge_workflow()`'s shape: a self-contained workflow paths-filtered to `openspec/**`, running `openspec validate --all --strict`, with a comment noting the unpinned `@fission-ai/openspec@latest` CLI-version tradeoff. (Requirement: Scaffold a portable openspec-validate workflow)
- [ ] 1.2 Determine the job's exact display name (its `name:` field) so `discover_ci_checks()` and the ruleset entry use identical text.

## 2. State detection and required_status_checks wiring

- [ ] 2.1 Add `openspec_validate_workflow_exists` to `detect_state()`, mirroring `automerge_workflow_exists`, so the "will be newly written this run" predicate is known before the ruleset loop runs. (Requirement: Idempotent on workflow-file presence, not solely on openspec_initialized)
- [x] 2.2 Give `build_ruleset_for_branch()` (or its `build_ruleset()` caller) a way to include an extra `required_status_checks` entry, used only when generating a *fresh* ruleset file and the openspec-validate workflow is newly written this run — the only caller-supplied entry; no other entry from `state["ci_jobs_discovered"]` is ever passed in. (Requirement: Wire the new check into required_status_checks, scoped to this job only — Scenario: New workflow written this run, ruleset file not yet present; Scenario: Other CI jobs remain unaffected)

## 3. Existing-ruleset patch path

- [ ] 3.1 Add a small in-place JSON patch step in `cmd_propose`, run only when the openspec-validate workflow is newly written this run and a given branch's `protect-<branch>.json` already exists: locate (or create) the `required_status_checks` rule and append `{"context": <job name>}` only if absent, leaving the rest of the file untouched. (Requirement: Wire the new check into required_status_checks, scoped to this job only — Scenario: New workflow written this run, ruleset file already exists)
- [ ] 3.2 No-op when the entry is already present in an existing ruleset file's `required_status_checks`. (same Requirement — Scenario: same, second AND clause)

## 4. propose() write step and reporting

- [ ] 4.1 Add the openspec-validate workflow write step to `cmd_propose`, gated on `state["openspec_validate_workflow_exists"]`, matching the existing automerge write step's `written`/`skipped` bookkeeping. (Requirement: Scaffold a portable openspec-validate workflow — Scenario: Fresh repo; Scenario: Already-onboarded repo, workflow missing; Scenario: Workflow already present)
- [ ] 4.2 Record any ruleset file freshly generated with the check included, and any existing ruleset file patched by task 3.1, in `written` (or a clearly-labeled equivalent) so the CLI's human-facing report shows what changed.

## 5. Tests

- [ ] 5.1 Extend `tests/onboarding/test_repo_init.py`: fresh repo run writes both the workflow and a ruleset with the required check. (Requirement: Scaffold a portable openspec-validate workflow — Scenario: Fresh repo)
- [ ] 5.2 Already-onboarded repo (openspec initialized, workflow absent, ruleset files already exist): run writes the workflow and patches the existing ruleset files to add the check, without altering their other rules. (Requirement: Wire the new check into required_status_checks, scoped to this job only — Scenario: New workflow written this run, ruleset file already exists)
- [ ] 5.3 Re-running `propose` after the workflow already exists: no changes to the workflow file or to `required_status_checks` (idempotent no-op). (Requirement: Idempotent on workflow-file presence, not solely on openspec_initialized — Scenario: Re-running propose on a fully onboarded repo; Requirement: Wire the new check... — Scenario: Workflow already present, not newly written)
- [ ] 5.4 A pre-existing ruleset file whose `required_status_checks` already contains this job's exact display name is left byte-for-byte unchanged. (Requirement: Wire the new check into required_status_checks, scoped to this job only — Scenario: New workflow written this run, ruleset file already exists, second AND clause)
- [ ] 5.5 An unrelated CI job discovered via `discover_ci_checks()` never appears in `required_status_checks` as a side effect of this change. (Requirement: Wire the new check into required_status_checks, scoped to this job only — Scenario: Other CI jobs remain unaffected)
- [ ] 5.6 [e2e] `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` both green.

## 6. Plugin/doc surface

- [ ] 6.1 Update `src/worktrail/onboarding/repo_init.py`'s module docstring's `propose` description if it now needs a caveat about this one scoped `required_status_checks` exception, so a future reader doesn't take the "deliberately never auto-populates" line as still-unconditional.
