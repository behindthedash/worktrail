## Why

Live incident `go-20260814-140159` (datalena) surfaced two independent
orchestrator defects that both caused wrong PR outcomes without any code
crashing: (1) a tail-kind (`e2e`/`cleanup`) task with an empty `files:` scope
gets dispatched with the vague fallback `"(see task file)"`, so the worker has
no signal that zero files are expected to change — it re-derives and
reimplements the whole spec from a fresh checkout and opens a near-duplicate
PR; (2) `integrate.py`'s operator-PR discovery fallback does a free-text
`gh pr list --search "<group-name> <spec-id>"` and blindly accepts
`matches[0]` with no check that the matched PR's head/base branch actually
belongs to this group, so an unrelated pre-existing PR (`#1990`, a
`stg`→`prd` release PR) was mistaken for the group's own PR and the real
implementation work was quarantined as "already has an open PR". Both are
silent-misrouting defects, not crashes, so they need explicit guards rather
than better error handling.

## What Changes

- `dispatch.py`'s `build_worker_prompt` gets an explicit no-op/verification
  branch for tail-kind (`e2e`/`cleanup`) tasks whose `files:` list is empty:
  the rendered prompt states plainly that zero files are expected to change
  and that the task is verification-only against the already-integrated
  base, replacing the current silent `scope = "(see task file)"` fallback
  for this specific case. Non-tail tasks and tail tasks that do carry a
  `files:` list are unaffected.
- The group/task integration flow SHALL NOT open a new PR for a tail task
  when its worker made no commits (zero files changed) — the existing
  `tail-task-auto-reconciliation` reconciliation path already only fires
  when a terminal tail task's own commits exist and never merged; this
  change ensures the zero-file case never reaches that path with fabricated
  commits in the first place, by making the no-op instruction explicit
  enough that a compliant worker makes none.
- `integrate.py`'s operator-PR-discovery fallback (`gh pr list --search`)
  gets a branch-correspondence check: a candidate match is only accepted
  when its `headRefName` equals the group's own branch (`gb =
  f"{run_id}/{name}"`) or its base branch matches the group's target
  (`pr_base`). A match that fails both checks is rejected and the flow falls
  through to normal `gh pr create`, exactly as if discovery had found
  nothing.
- Regression test coverage for both: a files-empty tail task's rendered
  worker prompt no longer contains the bare `"(see task file)"` fallback and
  instead carries an explicit no-op/verification-only instruction; PR
  discovery rejects a search match whose head/base branch doesn't
  correspond to the group and falls through to `gh pr create`.

## Capabilities

### New Capabilities
- `tail-task-noop-dispatch`: dispatch-time handling for tail-kind (e2e/cleanup)
  tasks with an empty `files:` scope — the worker prompt must state the task
  is verification-only with zero expected file changes, and the integration
  flow must not open a PR when the worker made no commits.
- `operator-pr-discovery-branch-guard`: branch-correspondence validation for
  `integrate.py`'s free-text operator-PR-discovery fallback, so a fuzzy
  `gh pr list --search` match is only accepted when its head or base branch
  actually corresponds to the group being integrated.

### Modified Capabilities
(none — both fixes are new guard behavior around existing dispatch/integrate
code paths; no existing spec's requirements change)

## Impact

- `src/worktrail/orchestrator/dispatch.py` (`build_worker_prompt`, the
  `scope`/`ROLE_CLEANUP` rendering path)
- `src/worktrail/orchestrator/integrate.py` (`integrate_one`, the operator-PR
  discovery block around the `gh pr list --search` call; possibly
  `_do_journal`/no-PR bookkeeping for the zero-file tail case)
- `tests/orchestrator/test_dispatch.py` / `test_dispatch_extras.py`
- `tests/orchestrator/test_integrate.py` / `test_integrate_extras.py`
- No interaction with `tail-task-auto-reconciliation` (`openspec/specs/`)
  beyond ensuring its precondition — the tail task's own unmerged commits —
  never gets fabricated by a misled worker in the first place.
