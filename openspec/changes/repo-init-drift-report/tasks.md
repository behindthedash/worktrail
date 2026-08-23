## 1. Structural comparison helper

- [x] 1.1 Add `_ruleset_structural_view()` to `src/worktrail/onboarding/repo_init.py`: deep-copy a ruleset dict and strip any `required_status_checks` rule entirely. (Requirement: Ruleset drift excludes required_status_checks)

## 2. Drift computation

- [x] 2.1 Add `_content_drift(repo, relpath, current_content)`: full-content comparison, returns a `{"path", "detail"}` entry or `None`. (Requirement: Content-only comparison for workflow and script templates)
- [x] 2.2 Add `_ruleset_drift(repo, branch, branch_model)`: structural comparison via `_ruleset_structural_view`, using `build_ruleset_for_branch(branch, branch_model)` as the baseline. (Requirement: Ruleset drift excludes required_status_checks)
- [x] 2.3 Add `compute_drift(repo, state, branches, branch_model)`: checks ruleset files (via `state["existing_rulesets"]`), the rulesets-sync script, requirements.txt, the rulesets-drift-guard workflow, the auto-merge workflow, and the openspec-validate workflow -- each only when `state` says it already exists. (Requirement: Always-on drift computation, no new flags; Requirement: Hand-edited and third-party-owned files are out of scope)
- [x] 2.4 Add `rulesets_requirements_exists` to `detect_state()`, matching the existing per-file state keys, and use it in both `compute_drift()` and `cmd_propose()`'s own requirements.txt skip check.

## 3. Wire into propose() and --check

- [x] 3.1 Call `compute_drift()` in `cmd_propose()` after the write/skip pass, add its result as `drift` in the result dict (JSON and text-mode output), alongside `ci_jobs_discovered`. (Requirement: Always-on drift computation, no new flags)
- [x] 3.2 Include `drift` in `--check` mode's output too, computing `branches` before the early-return. (Requirement: Check mode includes drift, in Always-on drift computation)
- [x] 3.3 [e2e] Confirm no write/skip logic changed -- `compute_drift()` is purely additive and read-only; a drifted file is still skipped and left on disk exactly as before. (Requirement: Drift is report-only and never auto-applied)

## 4. Tests

- [x] 4.1 `_ruleset_structural_view`: confirm a ruleset with `required_status_checks` and one without are equal after stripping; confirm a genuine structural change (branch_model 2 vs 3) is still detected. (Requirement: Ruleset drift excludes required_status_checks)
- [x] 4.2 `compute_drift`: no drift when files match current templates; structural drift detected on `protect-<branch>.json` while an operator-added required check is not flagged; content drift detected for the auto-merge and openspec-validate workflows; policy.yaml/AGENTS.md never appear in drift regardless of content. (Requirement: Ruleset drift excludes required_status_checks; Requirement: Content-only comparison for workflow and script templates; Requirement: Hand-edited and third-party-owned files are out of scope)
- [x] 4.3 `cmd_propose()` integration: fresh repo reports no drift; a second run on an already-onboarded, unmodified repo reports no drift; a hand-edited/stale automerge workflow surfaces in `drift`, is never rewritten, and still appears in `skipped`; `--check` mode includes the same `drift` entry. (Requirement: Always-on drift computation, no new flags; Requirement: Drift is report-only and never auto-applied)
- [x] 4.4 [e2e] `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` both green.
