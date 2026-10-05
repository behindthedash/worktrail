## Why

Runs constantly surface small, verified defects in files the run is already editing, and the
current defaults force every one of them into the backlog as its own brief with its own full
pipeline. Specimen: change `active-conflicts-staleness-reconciliation-missing-root-fail-loud`
(PR #1431) was editing `src/worktrail/router/run_record.py`; its authoring stage found a
one-line defect in that same file (stale module docstring / missing contract file) and captured
it as brief `20261004-094713` instead of fixing it — a one-line fix now owes a whole
propose → compile → fan-out → review → PR → archive cycle. Backlog growth is the reported
symptom.

Mechanics verified in this checkout that force capture-only:

- `src/worktrail/orchestrator/dispatch.py:741,750` — the worker prompt renders
  `Scope (only touch these): {scope}` and the hard rule `Touch no files outside scope`; the
  report-back contract has no channel for declaring an adjacent, in-scope fix.
- `src/worktrail/orchestrator/worktree_guard_hook.py` — the PreToolUse guard denies
  Write/Edit targets that resolve outside the worker's worktree root. It is a **worktree
  containment** guard (keyed on `cwd`), not a per-file scope guard; declared-scope discipline
  itself is prompt-level plus reviewer-level today.
- `src/worktrail/orchestrator/live.py:301` (`_REVIEWER_SYSTEM_PROMPT`) — the reviewer "look[s]
  for bugs, missing tests, and **scope drift**"; an undeclared extra fix fails review.
- The one sanctioned escape hatch is the bounded fix-scope escalation
  (`_scope_escalation_files`, `live.py:3527`) — but it exists for files **outside** the task's
  declared scope. An adjacent defect in a file already **inside** scope has no sanctioned path
  at all, so capture is the only convention — at both the worker stage and the propose/authoring
  stage.

## What Changes

- Define the three-tier policy, encoded where each decision is actually made:
  - **Tier 1 — fold in (same change, same PR).** Allowed only when every condition holds:
    (a) a verified defect — reproduced or directly evidenced, never a hypothesis or a "while
    I'm here" cleanup; (b) the fix touches only files already in the unit's declared scope (a
    task's `files:` for workers; the change's own file surface at the authoring stage); (c)
    mechanical — restores documented/established intent, no new design, API, or behavior
    contract (behavior-contract changes are Route G, never fold-ins). Capped at **2 fold-ins
    and ~20 changed lines per task**; beyond that, capture instead. Fold-ins go in their **own
    commit(s)** and are declared.
  - **Tier 2 — capture, and let the existing queue triage `fold-into-change` route it.**
    Unchanged triage verdicts; this change only stops tier-1 candidates from being paid out
    here.
  - **Tier 3 — standalone brief (Route F)** for everything else.
- **Worker prompt** (implement and fix roles only; clean and review role instructions are not
  widened): a fold-in clause — a verified, mechanical, in-scope defect is fixed in a separate
  commit and listed in the report-back; anything else is reported in `notes` (capture is the
  orchestrator's / authoring stage's job, never a scope expansion).
- **Report-back schema**: new OPTIONAL `"fold_ins": [{"file": "<path>", "commit": "<sha>",
  "summary": "<one line>"}]`. A report with no fold-ins parses exactly as today (absent key =
  empty list, never an error); parsed fold-ins are recorded on that task's run-journal entry.
- **Code validation (the teeth)**: every `fold_ins[].file` must be a member of the task's
  declared `files:` scope. A violation fails the task through the same terminal path as a
  failed review — never silently accepted.
- **Reviewer prompt + gate**: the reviewer SHALL validate each declared fold-in (in-scope,
  mechanical, tests still pass) and SHALL still FAIL undeclared out-of-scope edits or
  unexplained drift exactly as today. Declared, validated fold-ins are not scope drift.
- **Authoring-stage doctrine** (`skills/worktrail-sdd-workflow/SKILL.md`): at propose/authoring
  time, when a found defect's file is already in the change's own file surface, fold in (extend
  a task's scope or add a task) instead of capturing a brief; the three tiers are documented
  there.
- **Shared PR template** (`render_pr_body`, `src/worktrail/router/land_pr.py`): a
  `## Fold-in Fixes` section listing declared fold-ins, rendering `none` when there were none;
  the `land-pr` CLI gains the flag that supplies them.

## Capabilities

### New Capabilities

- `worker-fold-in-policy`: the three-tier fold-in policy — implement/fix worker prompt clause,
  the optional `fold_ins` report-back field and its journal recording, code-enforced
  declared-scope validation with fail-closed terminal semantics, the reviewer's fold-in
  validation duty, and the authoring-stage fold-in doctrine.

### Modified Capabilities

- `pr-landing-pipeline`: the "Standard PR body" requirement gains the `## Fold-in Fixes`
  section (rendering `none` when empty), keeping the template's section inventory in step with
  the enforced renderer.

## Impact

- **Code**: `src/worktrail/orchestrator/dispatch.py` (implement/fix clauses in `_ROLE_ACTION`,
  report-back schema + `fold_ins` scope validation in the report-application path),
  `src/worktrail/orchestrator/live.py` (`_REVIEWER_SYSTEM_PROMPT`, journal-entry construction
  recording `fold_ins`), `src/worktrail/router/land_pr.py` (`render_pr_body`, `LandRequest`,
  `land-pr` CLI).
- **Skills/docs**: `skills/worktrail-sdd-workflow/SKILL.md` (three-tier doctrine +
  authoring-stage fold-in default).
- **Tests**: `tests/orchestrator/test_dispatch.py`, `tests/orchestrator/test_dispatch_extras.py`,
  `tests/orchestrator/test_live_extras.py`, `tests/router/test_land_pr.py` (extended, not
  replaced — including the absent-notes byte-identity guarantee).
- **Explicitly unchanged**: `worktree_guard_hook.py` (no widening — fold-ins strictly inside
  declared scope is what preserves parallel fan-out safety), route classification and triage
  verdicts (`fold-into-change` is reused as-is), and the pre-commit/test gates (a fold-in with
  failing tests is a failed task).
