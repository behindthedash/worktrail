## 1. Template and builder

- [x] 1.1 Create `src/worktrail/onboarding/pull_requests_doc_template.py` holding the doc prose as `PULL_REQUESTS_DOC_TEMPLATE` with `__MERGE_METHODS__` and `__PROMOTION_SECTION__` markers, plus `PROMOTION_SECTION` and the AGENTS.md section text. Use `gh api repos/{owner}/{repo}/...` for the REST commands, never a literal slug (design D2).
  Implements requirement: propose scaffolds the PR labels doc
- [x] 1.2 In `repo_init.py` add `PULL_REQUESTS_DOC_RELPATH` and `build_pull_requests_doc(branches)`, rendering the merge-method list from `merge_method_for_branch` and the promotion section only for branches other than `dev` and `main`.
  Implements requirement: the doc matches the repo's branch model

## 2. Wiring

- [x] 2.1 Add `pull_requests_doc_exists` and `agents_md_links_pull_requests_doc` to `detect_state()`.
  Implements requirement: propose scaffolds the PR labels doc
- [x] 2.2 Add `ensure_agents_md_pr_pointer(repo)` that inserts the section before the first `<!-- name:start -->` marker or appends it, and is a no-op when `AGENTS.md` already mentions the doc path.
  Implements requirement: propose links the doc from AGENTS.md
- [x] 2.3 In `cmd_propose()` add the write-if-absent doc block and the pointer step, after the AGENTS.md split and the auto-merge workflow blocks. Do not add the doc to `compute_drift`.
  Implements requirement: the doc is excluded from drift reporting

## 3. Tests

- [x] 3.1 In `tests/onboarding/test_repo_init.py`, cover the doc: written on a fresh repo, skipped and byte-identical when present, every `AUTOMERGE_LABELS` name present, no `behindthedash` slug, `--check` state keys and no writes.
  Implements requirement: propose scaffolds the PR labels doc
- [x] 3.2 Cover the branch-model rendering for `["dev","prd"]`, `["dev","stg","prd"]` and `["main"]`.
  Implements requirement: the doc matches the repo's branch model
- [x] 3.3 Cover the AGENTS.md pointer: before an aspens block with the block unchanged, appended when no block, no-op when already mentioned, exactly once after two `propose` runs.
  Implements requirement: propose links the doc from AGENTS.md
- [x] 3.4 Cover that a customized doc does not appear in `drift`.
  Implements requirement: the doc is excluded from drift reporting

## 4. Documentation and release

- [x] 4.1 Update `skills/worktrail-repo-init/SKILL.md`: add the doc and the AGENTS.md pointer to the Overview list and a short Step 2 paragraph.
- [x] 4.2 [e2e] Run `pytest -q` and `python3 -m worktrail.orchestrator.orchestrate check`; both green.
