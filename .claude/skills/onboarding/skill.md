---
name: onboarding
description: worktrail-repo-init scaffolding — write-if-absent files, the agent-facing PR labels doc, and AGENTS.md pointer handling for src/worktrail/onboarding
triggers:
  files:
    - src/worktrail/onboarding/**
    - skills/worktrail-repo-init/**
    - tests/onboarding/**
  keywords:
    - repo-init
    - repo_init
    - propose
    - scaffold
    - pull-requests.md
    - go:risk
---

You are working on **`worktrail-repo-init`**, which bootstraps/migrates a repo onto the repo-standards doctrine (`src/worktrail/onboarding/repo_init.py`). `propose` scaffolds files; `apply` makes the GitHub API calls (e.g. creating the `AUTOMERGE_LABELS`).

## Domain purpose
The scaffolded auto-merge workflow **fails closed**: a green PR with no `go:risk-low` / `go:risk-medium` label is never armed and sits unmerged with `mergeStateStatus: CLEAN` and no error. Nothing in an onboarded repo told agents this, so `propose` now also scaffolds an agent-facing doc and links it from `AGENTS.md`.

## Business rules / invariants
- **Scaffolded files are write-if-absent.** `propose` reports them under `written` / `skipped`; an existing (possibly hand-tailored) file is left byte-identical.
- **`docs/engineering/pull-requests.md` is rendered per branch model** by `build_pull_requests_doc(branches)`: the merge-method list comes from `merge_method_for_branch` (the same function the rulesets and auto-merge workflow use), and the promotion-PR rule (hold with `go:no-automerge`) appears only when the model has a branch outside `SQUASH_ONLY_BRANCHES` (`dev`/`main`).
- **The doc is repo-slug-free** — REST commands use `gh api repos/{owner}/{repo}/...`, which `gh` expands from the checkout. Never embed an owner/repo.
- **Label names are not duplicated in code.** The doc names the five labels literally; a test asserts every name in `AUTOMERGE_LABELS` appears in the rendered doc, so adding/renaming a label fails the build until the template is updated.
- **The doc is deliberately excluded from `compute_drift`** — it is prose an operator may tailor.

## Non-obvious behaviors
- `ensure_agents_md_pr_pointer(repo)` inserts a "Pull requests" section into `AGENTS.md` **before the first tool-managed block** (`<!-- name:start -->`, matched by `_TOOL_BLOCK_START_RE`) so blocks like aspens' stay last; with no block it appends. It is a no-op when `AGENTS.md` already contains `PULL_REQUESTS_DOC_RELPATH`, and returns `(changed, warning)` — a missing `AGENTS.md` yields a warning, never a created file.
- The pointer is added **even when the doc file already existed** (the two steps are independent in `cmd_propose`).
- `detect_state()` reports `pull_requests_doc_exists` and `agents_md_links_pull_requests_doc`; `propose --check` reports them without writing.
- `build_pull_requests_doc` lives in `repo_init.py`, not the template module, because it needs `merge_method_for_branch` and `pull_requests_doc_template.py` cannot import `repo_init` without a cycle.
- Template modules (`*_template.py`) hold prose as string constants with `__MARKER__` placeholders rather than packaged data files, to avoid package-data resolution at runtime.
- The doc's guidance says to set labels via the REST API (`gh api .../issues/<n>/labels`) because `gh pr edit --add-label` fails on this account.

## Critical files (purpose, not inventory)
- `src/worktrail/onboarding/repo_init.py` — CLI: `detect_state`, `cmd_propose`, ruleset/workflow builders, label creation
- `src/worktrail/onboarding/pull_requests_doc_template.py` — doc prose, `PROMOTION_SECTION`, `AGENTS_MD_PR_SECTION`
- `skills/worktrail-repo-init/SKILL.md` — the user-facing procedure; keep in step with what `propose` writes
- `tests/onboarding/test_repo_init.py` — `PullRequestsDocTests` covers rendering, pointer placement, re-run idempotence, and drift exclusion

## Critical Rules
- Never overwrite an existing scaffolded file or add it to drift reporting without a deliberate decision.
- Keep `AGENTS.md` tool-managed blocks last; never interleave the PR section into one.

---
**Last Updated:** 2026-09-20
