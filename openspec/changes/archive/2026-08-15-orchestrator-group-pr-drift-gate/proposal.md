## Why

Orchestrator-created group PRs skip every deterministic pre-PR drift check.
`pre_pr_gate.py`'s `--labels-only` branch (`src/worktrail/router/pre_pr_gate.py:352-361`)
returns 0 as soon as it prints the resolved labels — *before* `spec_sync_drift`,
`check_changed_specs` (clarification-integrity), `check_dod_failures`, and
`check_req_coverage_failures`. `integrate.py`'s only `pre_pr_gate` call site is
`_refresh_pr_labels` (`src/worktrail/orchestrator/integrate.py:110-135`, sole caller at
`:1119`) and it always passes `--labels-only`. So no orchestrator group PR has ever run
those four checks.

Nothing backstops the hole: `pre_pr_gate.py` is the only gating caller of those check
functions, no consumer repo's `pre_pr_cmd`/`integrate_smoke_cmd` invokes a
`worktrail-check-*` command, and no CI workflow runs them (GGB vendors
`check_spec_sync.py` only). The worst case is DoD-verification, because the orchestrator
*manufactures that check's exact population itself*: `_write_group_task_status`
(`integrate.py:274-319`, called at `:1028`) writes `status: completed` into the group's
task files on the group branch. A one-off `/go` route that marks a task completed passes
through DoD verification via `worktrail-preflight`; the orchestrator marks it completed
mechanically and nothing re-verifies the claim.

The chronology says collateral, not deliberate: `--labels-only` shipped 2026-07-25
(`bf4a0fd`), `check_dod_failures` was wired 2026-07-30 (`b089883`), and
`check_req_coverage_failures` 2026-08-09 (`8871a50`) — both inserted after the early
return without revisiting the orchestrator call site.

## What Changes

- **New `--checks-only` mode on `pre_pr_gate.py`** that runs exactly the four
  deterministic drift checks — spec-sync, clarification-integrity, DoD-verification, and
  req/AC coverage — against the given `--repo` and returns their existing exit codes
  (`1`, `3`, `4`, `5`), then exits 0. It deliberately does **not** run `pre_pr_cmd`, does
  not run the docs-only bypass, and does not run scope-completeness review.
- **`integrate.py` calls the new mode on every group**, blocking, immediately after
  `_write_group_task_status(iw, spec_id, g, status)` (`integrate.py:1028`) and before the
  `if smoke_cmd:` block — so DoD verification sees the completed-task population the
  orchestrator just wrote, and the cheap deterministic checks fail fast ahead of the
  expensive smoke command. Both run before the push at `:1036` and PR creation at `:1119`.
- **The call passes `--repo <integration worktree>`, not the canonical checkout.**
  `changed_paths()` (`pre_pr_gate.py:178-199`) shells `git merge-base HEAD <base>` and
  `git diff --name-only` with `cwd` set to `--repo`. `_refresh_pr_labels` passes
  `str(repo)` — the canonical checkout, whose `HEAD` is the base branch. That is harmless
  for labels (policy-only, no diff), but would make every drift check inspect the wrong
  tree and silently pass.
- **A drift failure quarantines the group** on the same path `_run_integration_smoke`
  failures already take: set `quarantined[name]` with a reason carrying a short tail of
  the gate's stderr, print the `SKIP` line, journal `QUARANTINED`, return `None`. No PR is
  created. A new quarantine reason code distinguishes this from generic
  `integration_error` (rationale in `design.md`).
- **Enforcement is blanket**, on every group, not just the spec-carrier group.
  Non-carrier groups have their spec folder reset by `_strip_spec_folder_to_base`
  (`integrate.py:254`), so the three spec-scoped checks no-op naturally on them;
  DoD-verification's population spans *all* groups via `_write_group_task_status`, so a
  carrier-only scope would leave the worst case partly uncovered.
- **Correct the stale assertion in `auto-dod-verification/proposal.md`** (lines 31-34,
  57, 74-76), which calls derived DoD checks "opt-out-free" and asserts the existing
  diff-scoped `pre_pr_gate.py` wiring covers them with no changes to `pre_pr_gate.py`
  itself. True for one-off routes, false for orchestrator PRs until this change lands.
  That change is 20/20 tasks complete and unarchived, so the two must be made consistent.
- **Not changed**: `--labels-only`'s contract and its existing callers. The new mode is
  added alongside it.

## Capabilities

### New Capabilities
- `orchestrator-group-pr-drift-gate`: every orchestrator-created group PR must clear the
  four deterministic pre-PR drift checks, evaluated against the group's own integration
  worktree, before it is pushed or opened; a failure quarantines the group instead of
  opening a PR.

### Modified Capabilities
(none — no existing `openspec/specs/` capability covers `pre_pr_gate.py`'s modes or
`integrate_one`'s pre-PR sequence. `devkit-requirement-coverage-gate` and
`openspec-requirement-coverage-gate` describe what the coverage checks *assert*, not
where they are invoked from, and are unchanged by this proposal.)

## Impact

- `src/worktrail/router/pre_pr_gate.py`: new `--checks-only` flag; the four drift checks
  are extracted into a reusable `run_drift_checks(repo, policy)` helper so both the
  default path and the new mode run byte-identical logic; module docstring updated with
  the new mode and its exit codes.
- `src/worktrail/orchestrator/integrate.py`: new `_run_drift_gate(iw, name, route, ...)`
  helper wrapping the subprocess call; new `QUARANTINE_DRIFT_GATE` reason constant; new
  call site in `integrate_one` between `_write_group_task_status` and the `smoke_cmd`
  block.
- `tests/router/test_pre_pr_gate.py`: the new mode runs the drift checks and does **not**
  execute `pre_pr_cmd`; existing `--labels-only` tests must pass unchanged.
- `tests/orchestrator/test_integrate.py`: the gate receives the integration worktree
  (`iw`) as `--repo`, not the canonical repo; a drift failure quarantines the group and
  creates no PR; a clean group still opens its PR unchanged.
- `openspec/changes/auto-dod-verification/proposal.md`: annotate the "opt-out-free" /
  "no changes to `pre_pr_gate.py`" assertions.
- No version bump in this PR. `CI: Version Bump Check` fires for any PR touching
  `src/worktrail/**`, but `AGENTS.md` (Versioning) requires the bump to be a standalone
  `chore: bump Worktrail to X.Y.Z` commit spanning `pyproject.toml` and
  `.codex-plugin/plugin.json`, "not bundled into a feature/fix PR", with periodic
  multi-PR batches as the repo's stated practice. This PR therefore carries the
  `go:no-version-bump` label and the bump lands in the next batch. No new console
  script — `worktrail-pre-pr-gate` already exists.
- **Out of scope, deliberately**: `scope_review_failures` / scope-completeness (gated on
  `--run`, which the orchestrator has no per-group equivalent of — owned by briefs
  `20260731-145729` and `20260815-134233`); the systematic gate-parity audit and its
  regression selfcheck (brief `20260815-134233`, sequenced after this change).
