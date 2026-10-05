## Why

`worktrail-repo-init propose` recognizes only the renamed
`.github/workflows/worktrail-auto-merge.yml`. Fourteen of the sixteen onboarded
repositories surveyed for work-queue brief `20260924-043952-repo-init-propose-recognizes-only`
instead carry the earlier `.github/workflows/auto-merge.yml` name. Re-running the documented
back-fill path (`propose`) in one of those repositories writes a second workflow with the same
`CI: Auto-merge on open` display name. The legacy workflow is also invisible to drift reporting,
and a newly scaffolded PR-label document names the new file rather than the workflow actually
running.

The old filename is a supported historical artifact, not evidence that a repository should be
silently migrated. `propose` must recognize it without changing it.

## What Changes

- Treat `.github/workflows/auto-merge.yml` as a legacy auto-merge workflow alongside the
  canonical `.github/workflows/worktrail-auto-merge.yml`.
- When either name exists, `propose` skips writing another auto-merge workflow, preserves the
  existing file byte-for-byte, and `apply` ensures the workflow's risk labels are present.
- Include every recognized existing auto-merge workflow in report-only content drift. When both
  names exist, retain both files and prefer the canonical path for new prose.
- Render a newly written `docs/engineering/pull-requests.md` with the path of the effective
  existing workflow, so a legacy-only repository's instructions identify `auto-merge.yml`.
- Update the repo-init skill to describe the legacy-name compatibility behavior.

### Explicitly not in this change

- No rename, deletion, or automatic migration of a legacy workflow.
- No rewrite of an existing workflow or PR-label document, including one that names the other
  workflow path.
- No change to the workflow contents, auto-merge policy, label definitions, or risk classifier.

## Capabilities

### New Capabilities

- `repo-init-automerge-workflow-compatibility`: repo initialization recognizes both generations
  of its auto-merge workflow without duplicating or silently migrating them.

### Modified Capabilities

- `repo-init-drift-report`: content drift covers recognized legacy auto-merge workflow paths.
- `repo-init-pr-labels-doc`: a newly scaffolded doc identifies the effective auto-merge workflow.

## Impact

- `src/worktrail/onboarding/repo_init.py` and
  `src/worktrail/onboarding/pull_requests_doc_template.py`.
- `tests/onboarding/test_repo_init.py`.
- `skills/worktrail-repo-init/SKILL.md`.
