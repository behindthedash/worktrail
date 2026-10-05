## Why

`worktrail-repo-init` scaffolds the auto-merge workflow and creates the five `go:risk-*` /
`go:no-automerge` labels, but nothing tells an agent working in the onboarded repo that every PR
needs one of those labels. The workflow fails closed: a green PR with no `go:risk-low` /
`go:risk-medium` label is never armed and sits unmerged with `mergeStateStatus: CLEAN` and no
error. Agents that open PRs with a bare `gh pr create` hit this silently.

Verified 2026-09-20 on `behindthedash/wake-up-sooner` (PR #36): the repo had the workflow and the
labels but no in-repo instructions, so the guidance was hand-written into
`docs/engineering/pull-requests.md` plus a pointer in `AGENTS.md`. Every other onboarded repo has
the same gap, and every future onboarding will recreate it.

## What Changes

- `propose` writes `docs/engineering/pull-requests.md` when absent: the label table, how to choose
  a tier, how to set / change / verify labels (REST API, because `gh pr edit --add-label` fails on
  this account), the stale-label gotcha, the merge method per protected branch, and, for
  branch models that have a promotion branch, the rule to hold promotion PRs with
  `go:no-automerge`.
- `propose` adds a short "Pull requests" section to `AGENTS.md` pointing at that doc, placed before
  any tool-managed block so those blocks stay last.
- The doc is repo-slug-free (`gh api repos/{owner}/{repo}/...`), so the same text is correct in
  every repo.
- `skills/worktrail-repo-init/SKILL.md` documents the new artifact.

### Explicitly not in this change

- No change to the auto-merge workflow, the labels `apply` creates, or the risk classifier.
- No drift reporting for the doc: it is prose an operator may legitimately tailor, so a template
  diff would be noise (`compute_drift` already excludes hand-edited files).
- No back-fill of already-onboarded repos beyond re-running `propose`, which writes it
  when absent.

## Capabilities

### New Capabilities
- `repo-init-pr-labels-doc`: a scaffolded repo carries agent-facing instructions for the PR risk
  labels the auto-merge workflow depends on.

## Impact

- `src/worktrail/onboarding/repo_init.py`, `src/worktrail/onboarding/pull_requests_doc_template.py` (new).
- `skills/worktrail-repo-init/SKILL.md`.
- `tests/onboarding/test_repo_init.py`.
- No behavior change for an already-onboarded repo: both writes are write-if-absent.
