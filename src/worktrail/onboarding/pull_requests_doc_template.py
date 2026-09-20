"""Agent-facing PR label instructions that `worktrail-repo-init propose` scaffolds
into `docs/engineering/pull-requests.md`, plus the short `AGENTS.md` section that
points at it.

Why this exists: the scaffolded auto-merge workflow fails closed. A green PR with no
`go:risk-low` / `go:risk-medium` label is never armed and sits unmerged with
`mergeStateStatus: CLEAN` and no error, so an agent that opens a PR with a bare
`gh pr create` needs to be told, in the repo, that the label is required and how to set
it.

The text is a string constant rather than a packaged data file, matching the other
`*_template.py` modules (no package-data resolution at runtime). It carries no
repository owner or name: `gh api` expands `{owner}` and `{repo}` from the current
checkout, so the same text is correct in every repo. Two parts depend on the branch
model and are filled by `repo_init.build_pull_requests_doc()`, which lives there because
it needs `merge_method_for_branch` and this module cannot import `repo_init` without a
cycle.
"""

from __future__ import annotations

MERGE_METHODS_MARKER = "__MERGE_METHODS__"
PROMOTION_SECTION_MARKER = "__PROMOTION_SECTION__"

PULL_REQUESTS_DOC_TEMPLATE = f"""\
# Pull Requests and Auto-Merge Labels

Every PR needs exactly one `go:risk-*` label. The `CI: Auto-merge on open` workflow (`.github/workflows/worktrail-auto-merge.yml`) reads those labels to decide whether GitHub-native auto-merge is armed. **An unlabeled PR is never armed**: every check goes green, `mergeStateStatus` shows `CLEAN`, and the PR just sits there. Nothing fails, so it is easy to miss.

## Labels

| Label | Meaning | Auto-merge |
| --- | --- | --- |
| `go:risk-low` | Low risk tier | Armed |
| `go:risk-medium` | Medium risk tier | Armed |
| `go:risk-high` | High risk tier | Never — human merge |
| `go:risk-critical` | Critical risk tier | Never — human merge |
| `go:no-automerge` | Not eligible for auto-merge | Never — overrides any risk label |

The ceiling is `automerge.max_risk` in `.worktrail/policy.yaml`. The workflow arms auto-merge when the PR has `go:risk-low` or `go:risk-medium` **and** no `go:no-automerge`. It re-runs on every `labeled`/`unlabeled` event and disarms auto-merge whenever the PR becomes ineligible.

## Choosing a tier

Pick the highest tier that applies. The worktrail classifier's keyword heuristics are the reference:

- **critical** — billing/payments, secrets/credentials/API keys, destructive data operations (drop/truncate/wipe), weakening auth.
- **high** — authentication/authorization/roles/permissions, database migrations or schema changes, PII/passwords/encryption.
- **medium** — API endpoints/contracts, database access, schema.
- **low** — everything else: copy, styling, docs, refactors with no interface change. A docs-only diff is always `low`.

When unsure between two tiers, take the higher one. Use `go:no-automerge` (alongside the risk label) when a human should look at the PR regardless of tier.
{PROMOTION_SECTION_MARKER}
## Setting the label

**Preferred:** open the PR with `worktrail-land-pr`, which resolves the tier and applies the label for you.

**Hand-rolled `gh pr create`:** pass the label at creation time.

```bash
gh pr create --base <branch> --label go:risk-low --title "..." --body "..."
```

`gh pr create --label` fails outright if the label does not exist in the repo. `worktrail-repo-init apply` creates all five; if any were deleted, recreate them before opening a PR.

**Adding or removing a label on an existing PR:** use the REST API. `gh pr edit --add-label` fails on repos that still have a Projects (classic) board attached.

```bash
# add
gh api repos/{{owner}}/{{repo}}/issues/<n>/labels -X POST -f "labels[]=go:risk-medium"

# remove
gh api repos/{{owner}}/{{repo}}/issues/<n>/labels/go:risk-low -X DELETE
```

When changing tiers, **remove the old risk label**. The workflow only checks that `go:risk-low` or `go:risk-medium` is present, so a stale `go:risk-low` left next to `go:risk-high` still arms auto-merge. Add `go:no-automerge` while you fix it.

## Verify before walking away

```bash
gh pr view <n> --json labels,autoMergeRequest
```

For a `low`/`medium` PR, `labels` must contain the risk label and `autoMergeRequest` must be non-null once the workflow has run. Green checks alone do not mean the PR will merge.

## Merge method

Each protected branch allows one merge method, and the workflow picks it from the PR's base branch:

{MERGE_METHODS_MARKER}
"""

PROMOTION_SECTION = """\

### Promotion PRs

The rulesets scaffolded for this repo require 0 approving reviews, so the label is the only human gate on a promotion PR into __PROMOTED__. Label those PRs `go:no-automerge` and merge them by hand.
"""

AGENTS_MD_PR_SECTION = """\
## Pull requests

Every PR needs a `go:risk-*` label (`go:risk-low`, `go:risk-medium`, `go:risk-high`, `go:risk-critical`, plus `go:no-automerge` to hold). Without `go:risk-low` or `go:risk-medium` the auto-merge workflow never arms and a green PR sits unmerged. Set the label when you open the PR and verify it. See `docs/engineering/pull-requests.md` for how to choose a tier and how to set, change, and verify labels.
"""
