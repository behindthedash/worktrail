## Why

Every defect brief rides the full modify pipeline, even when it reduces to one mechanical
fix. Specimen: change `active-conflicts-staleness-reconciliation-missing-root-fail-loud`
(PR #1431, brief 20261003-162105) was one task across two declared files
(`src/worktrail/router/run_record.py`, `tests/router/test_run_record.py`) with ~15 changed
source lines — yet it paid a full orchestrated run (compile → single-worker fan-out with a
task worktree → integrate → CI, ~35 minutes). Route F already sanctions a direct fix-branch
worktree with **no** orchestration for unspecced code (`routes.md` §F step 5); a spec-owned
mechanical fix — which still needs its OpenSpec change artifacts — has no equivalent, so the
defect-brief backlog drains slower than it grows.

## What Changes

- **A code-enforced eligibility gate** — new console script `worktrail-modify-direct-gate`
  (`src/worktrail/router/modify_direct_gate.py`) reads one change directory and returns
  `{"eligible": bool, "reason": str, "task_count": int, "files": [...]}`. Eligible only when
  the change is exactly 1 task, its declared file set is present and within the file cap,
  its estimated delta is within the changed-line cap (lines the change's delta specs
  introduce beyond the base spec they modify), and no declared file touches the
  routing/classification surface (classifier, risk/policy modules, routing cassettes). The
  verdict is the decision — the pipeline branches on the script's JSON, never on prose
  judgment — and a nonzero exit never means "ineligible" (verdict convention, like the
  classifier).
- **A direct branch in the modify pipeline** (`skills/worktrail-sdd-workflow`: `SKILL.md` +
  `pipeline-details.md#modify-pipeline`): the gate runs before the compile step. When
  eligible, the executor implements the single task inline in the change worktree — the same
  trust level `routes.md` §F already extends to direct fix-branch worktrees — runs the
  unchanged compile, `openspec validate`, pre-PR gate, and CI, lands one PR via
  `worktrail-land-pr` carrying the change artifacts + implementation, then syncs/archives
  exactly as the orchestrated path. No orchestrator, no worker spawns, no task worktrees.
  When ineligible, the orchestrated path is unchanged and the gate's reason is surfaced.
- **Mode recorded on the run record** — one `decisions` entry naming direct-vs-orchestrated
  and carrying the gate's reason, so the audit trail shows why a run skipped fan-out.
- **The gate mirrors the fold-in bar** — the same conditions the sibling change
  `worker-fold-in-policy` (branch `spec/worker-fold-in-policy`, not yet merged) defines for
  tier-1 fold-ins: verified/mechanical, files already in declared scope, no new behavior
  contract — so "mechanical" means one thing system-wide. Thresholds live as named constants
  in the gate module; no policy knobs.
- **Docs** — `routes.md` §F step 5 (and §G's pointer to the pipeline) updated to name the
  direct branch for spec-owned mechanical fixes, which currently route to
  "single-worker orchestrate for 1-task fixes".

## Capabilities

### New Capabilities

- `modify-direct-mode`: the direct execution mode of the modify pipeline — the eligibility
  gate (conditions, caps, output/exit contract), the pipeline's direct branch and
  never-silent fallback, mode recording on the run record, and the explicit no-relaxation
  guarantee that compile, `openspec validate`, the pre-PR gate, and CI all still apply.

### Modified Capabilities

_None._ Route classification, risk/policy machinery, and the routing cassette are untouched
by this change — the direct-mode gate consumes declared change artifacts, it does not alter
routing behavior, so no existing requirement changes.

## Impact

- **Code**: new `src/worktrail/router/modify_direct_gate.py`; `pyproject.toml`
  `[project.scripts]` registration; new `tests/router/test_modify_direct_gate.py`.
- **Skills/docs**: `skills/worktrail-sdd-workflow/SKILL.md`,
  `skills/worktrail-sdd-workflow/references/pipeline-details.md` (`#modify-pipeline`),
  `skills/worktrail-go/references/routes.md` (§F, §G). `tests/test_plugin_surface.py` stays
  green because the new command token resolves to a real entry point.
- **Unchanged on purpose**: compile and its `.compile-ok` marker, `openspec validate`,
  `worktrail-land-pr`, the sync/archive step, the pre-PR gate, and CI — direct mode skips
  the orchestrator, never a gate. The accepted trade-off: a direct run forgoes the
  orchestrator's independent reviewer and worktree isolation for the capped single fix,
  which is the trust level the existing Route F direct fix-branch path already carries.
