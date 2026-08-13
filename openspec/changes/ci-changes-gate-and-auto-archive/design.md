## Context

`ci.yml` currently has one `pull_request`/`push` triggered job,
`lint-test-build`, that always runs `pytest`, the orchestrator golden
regression, and a `pip install build && python3 -m build`. The branch
ruleset (`.github/rulesets/protect-main.json`) requires three checks by
name: `Lint, Test & Build`, `Version bump check`, `Scope check`. Only
`Lint, Test & Build` is slow enough to matter here — `Version bump check`
and `Scope check` already run cheap scripts and `version_bump_check.sh`
already treats a diff with no `src/worktrail/**` change as not requiring a
bump, so a pure version bump PR already passes it trivially. See
proposal.md for the measured cost.

`pre_pr_gate.py`'s `is_docs_only()`/`docs_only_paths` already implements an
equivalent fast path for local preflight, diffing against a resolved base
ref with `fnmatch`-based glob patterns. This change adds the CI-side
equivalent as a required-status-check-preserving job, not a change to
`pre_pr_gate.py` itself.

On the archive side, `dashboard.scan()` (`src/worktrail/router/dashboard.py`)
already classifies a task-complete, verify-complete OpenSpec change as
`stage == "complete"` with `next_action == "archive"` (`_safe_detect_openspec`).
`drain.py`'s `REMEDIATION_TABLE` (`StageRemediation` dataclass: `key`,
`label`, `finder`, `action`) already has four rows following the same
finder/action shape, most recently `stale_bookkeeping` →
`close_stale_bookkeeping`, which opens a docs-only PR from a short-lived
fix-branch worktree. This change adds a fifth row reusing that exact
mechanic instead of introducing a new pattern.

## Goals / Non-Goals

**Goals:**
- Skip `Lint, Test & Build`'s slow steps for bookkeeping-only PR diffs
  while the required check still resolves green.
- Auto-open the OpenSpec archive PR once a change reaches `stage ==
  "complete"`, removing the hand-driven archive chore.

**Non-Goals:**
- Gating `Version bump check` or `Scope check` — both are already cheap and
  out of scope per the proposal.
- Optimizing the post-merge `push` trigger's CI run. The ceremony being cut
  is PR-blocking CI time; a `push` run to `main` is post-merge and always
  runs the full suite regardless of classification (see the merge-ref
  decision below).
- Auto-*merging* the archive PR. It still goes through the normal PR flow
  (review-thread-resolution requirement, required checks, and — if eligible
  — `auto-merge.yml`); this change only removes the manual step of opening it.
- Changing devkit-format specs' `stage == "complete"` semantics or behavior
  in any way — that stage means something different for devkit
  ("open PR / sync — verify merge state") and is explicitly excluded.

## Decisions

### Classification: `dorny/paths-filter` + a small version-only check, PR-only

Follow datalena's `qa-pipeline.yml` `changes` job pattern: a `changes` job
runs `dorny/paths-filter@v3` with `predicate-quantifier: every`, gated
`if: github.event_name == 'pull_request'`, with a fallback step for any
other trigger that sets the output to "not bookkeeping" (`code=true`
equivalent). This is the same shape used to avoid the merge-ref gotcha —
`paths-filter`'s default event-API diff mode assumes a PR base to diff
against; there is no equivalent base for a `push` event's diff (only
`github.event.before`, which is unreliable across force-pushes/merge
commits), so rather than reproduce that fragility, `push` runs always take
the "not bookkeeping" branch and run the full suite. Only the
`pull_request` trigger is optimized, which is where the ceremony savings
actually land (PRs blocking merge).

`pyproject.toml` cannot be classified by path pattern alone — a real
dependency/config edit must still run the full suite, but a pure version
bump must not. A path-filter match on `pyproject.toml` therefore only
narrows to "needs deeper check"; a small inline script step (reusing the
same `git diff -- pyproject.toml` shape and `+version = `/`-version = `
line-count discipline as `scripts/ci/version_bump_check.sh`) confirms the
change is version-line-only before folding it into the `bookkeeping`
output. Alternative considered: extend `paths-filter` itself with a content
matcher — rejected, `paths-filter` filters by path glob only, not diff
content.

### Required check via `checks.create` stub, not `if:` skip alone

Simply adding `if: needs.changes.outputs.bookkeeping == 'false'` to the
`lint-test-build` job would leave the `Lint, Test & Build` required check
perpetually pending on a bookkeeping-only PR (GitHub does not treat a
skipped job as satisfying a *required* status check by name). A second
`bookkeeping-bypass` job (mirroring datalena's `docs-only-bypass` job)
posts a `success` check for that exact name via
`actions/github-script` + `github.rest.checks.create` when the job is
skipped, so the ruleset's required-status-check rule resolves the same way
the local `pre_pr_gate.py` docs-only skip already tells a human to "record
this skip" — except automated here since it's driven by the actual diff,
not a human's PR-body note.

### Archive remediation: fifth `REMEDIATION_TABLE` row, not a new sweep function

`drain.py`'s remediation table is explicitly designed (see
`drain-stage-remediation-table` spec, "Data-driven remediation table") so a
new safe remediation category is one table entry, not a new hand-written
function plus new call sites. The archive row's finder scans
`dashboard.scan()` results filtered to `format == "openspec"` and
`stage == "complete"`; the action is a close cousin of
`close_stale_bookkeeping`: short-lived fix-branch worktree off the repo's
base branch, run `openspec archive -y <change-id>`, commit whatever the
archive command moved/wrote, push, `gh pr create --label go:risk-low`,
detect-and-return an already-open PR for re-entrancy, tear down the
worktree in a `finally`. Unlike `close_stale_bookkeeping`, there is no
"nothing to flip" no-op case — `openspec archive` always produces a
directory move (`openspec/changes/<id>/` → `openspec/changes/archive/...`),
so every finding either archives or hits the already-open-PR short-circuit.

**Critical scope guard:** `dashboard.scan()` reports `stage == "complete"`
for *both* format branches, but the meanings differ —
`_safe_detect_openspec` uses it for "ready to archive", while the devkit
branch (`detect_stage`, line ~988) uses the *same string* for "open PR /
sync — verify merge state", a fundamentally different and non-actionable
state. The finder MUST filter on `format == "openspec"` in addition to
`stage == "complete"`, or it would silently attempt to run
`openspec archive` against a devkit spec (which has no such directory
structure) the first time one existed. A dedicated scenario/test covers
this.

## Risks / Trade-offs

- [Silent stub masking a real check-name drift] If the branch ruleset's
  required check name for `Lint, Test & Build` ever changes without this
  workflow's stub name changing to match, the stub would post success under
  a name GitHub no longer requires, silently doing nothing useful (not
  unsafe, just dead code) → mitigated by using the literal job `name:`
  string already shared with the ruleset JSON, not a separate constant.
- [Pyproject version-only check false-negative] A hand-crafted diff that
  smuggles a second change alongside a version bump on the same line (e.g.
  editing `version = "1.2.3"  # comment`) could misclassify → mitigated by
  requiring the diff's *only* changed line match the version pattern
  exactly, the same strict check `version_bump_check.sh` already applies
  elsewhere in this repo.
- [Archive PR churn] A change that reaches `stage == "complete"` but whose
  human authors intended to keep it open a while longer (e.g. deliberately
  batching several changes before archiving) gets an unsolicited archive PR
  → mitigated by the PR being low-risk, reviewable, and closeable/declinable
  like any other bot-opened PR; this mirrors the accepted trade-off
  `close_stale_bookkeeping` already made for stale-bookkeeping closeout.

## Migration Plan

No data migration. Both changes are additive CI/drain behavior; existing
`REMEDIATION_TABLE` rows, `sweep_remediations()` callers, and CI required
checks are unaffected in shape (the summary dict only gains a new key, and
`checks.create` grows a new possible successful check instance, not a
new required name). Rollback is a plain revert of the two files.
