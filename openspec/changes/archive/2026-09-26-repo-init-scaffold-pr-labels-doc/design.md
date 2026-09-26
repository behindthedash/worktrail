## Context

`propose` already scaffolds worktrail-owned files write-if-absent and reports them under
`written` / `skipped`. The auto-merge workflow and `AUTOMERGE_LABELS` already exist in
`repo_init.py`. This change adds prose, not behavior.

## Decisions

**D1 — Doc text is a template module with two markers, rendered in `repo_init.py`.** The static
prose lives in `pull_requests_doc_template.py` (same posture as the other `*_template.py`
modules). Two parts depend on the branch model — the merge-method list and the promotion
section — so the template carries `__MERGE_METHODS__` and `__PROMOTION_SECTION__` markers that
`build_pull_requests_doc(branches)` fills. The builder lives in `repo_init.py` because it needs
`merge_method_for_branch`, which the template module cannot import without a cycle.

**D2 — No repo slug in the text.** `gh api` expands `{owner}` and `{repo}` from the current
checkout, so the REST commands are identical in every repo and the template needs no
per-repo substitution.

**D3 — Label names are checked against `AUTOMERGE_LABELS`, not duplicated.** The doc names the
five labels literally (an agent reads it verbatim), and a test asserts every name in
`AUTOMERGE_LABELS` appears in the rendered doc, so adding or renaming a label fails the build
until the doc is updated.

**D4 — AGENTS.md pointer goes before the first tool-managed block.** Doctrine keeps
tool-managed blocks (`<!-- x:start -->` … `<!-- x:end -->`) at the end of `AGENTS.md`, and some
tools regenerate them in place. The section is inserted before the first such marker, or
appended when there is none. An `AGENTS.md` that already mentions the doc path is left
byte-identical.

**D5 — No drift entry.** `compute_drift` documents that hand-editable files are out of scope;
this doc is prose an operator may tailor (repo-specific tier examples).

**D6 — No version bump here.** `AGENTS.md` batches bumps into a standalone `chore: bump Worktrail to X.Y.Z` PR, and `CI: Release Metadata Check` only validates PRs that change `pyproject.toml`'s version.

## Risks

- A repo whose `AGENTS.md` is not created by `propose` (both `AGENTS.md` and a non-shim
  `CLAUDE.md` exist) gets the doc but no pointer edit beyond what `AGENTS.md` already permits;
  the existing split warning already tells the operator to resolve by hand.
