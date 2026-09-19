## Why

`--evaluate-brief-triage` handles the "capacity ran out *during* the spawn" case and nothing
else. `grep -n "NoExecutionTarget" src/worktrail/router/skill_dispatch.py` returns exactly two
hits: the import (`:35`) and a single catch at `:1325-1326`, which is the `--resolve-routing`
branch. The `--evaluate-brief-triage` branch (`:1178-1199`) catches `BriefOwned`, `BriefMissing`,
`EmptyBrief`, `PendingDecision`, and `EvaluatorUnavailable` — not `NoExecutionTarget`.

That matters because `spawn_agent()` reaches its `exhausted`-flag returns only after it has
attempted at least one cell. Its **first** cell selection is unguarded: `cell = _select()`
(`spawnlib.py:1034`) calls `select_cell()`, which raises `NoExecutionTarget`
(`runtime/selection.py:88` builds the message; `:319`/`:411` raise) when every cell in the row is
already capacity-gated on entry. Nothing between there and `main()` catches it —
`evaluate_group()` (`queue_triage.py:1565`) calls `spawn_agent()` bare, `evaluate_briefs()` only
inspects `result.get("exhausted")` (`:1999`), and `evaluate_single_brief()` documents
`EvaluatorUnavailable` as the capacity outcome. So a brief whose repo has no live cell *before*
the spawn starts crashes the gate with a `NoExecutionTarget` traceback instead of printing the
documented `blocked_no_capacity: <repo>/<failure_class>: <detail>` line
(`skills/worktrail-go/SKILL.md:306`) and exiting 2.

The operator-visible difference is the whole defect: the documented contract says a capacity
block is a clean exit-2 with the brief left queued and untouched. A traceback out of `main()`
exits 1 — the code `worktrail-go` reads as "the evaluator ran and produced no identifiable
verdict" — so the same underlying condition is reported as two different things depending on
whether the gate closed a moment before the spawn or a moment after it. `cmd_evaluate()`
(batch `queue-triage evaluate`) has the same hole: it catches `EvaluatorUnavailable` per group
(`:3942`) and keeps every other group's verdicts, but a `NoExecutionTarget` from the first
selection aborts the entire run, discarding the verdicts of groups that already evaluated fine.

Archived change `2026-09-06-triage-evaluator-capacity-non-verdict` built the `exhausted`
flag and the `EvaluatorUnavailable`/exit-2 path for the exhausted-during-spawn case. This is
its uncovered sibling — exhausted-before-spawn — not a duplicate.

## What Changes

- **A pre-spawn selection failure becomes the same evaluator-unavailable outcome as a
  mid-spawn one.** `evaluate_group()` catches `NoExecutionTarget` around its `spawn_agent()`
  call and returns the exhausted group dict it already builds (`raw_text=""`,
  `"exhausted": True`, a failure class read off the exception), so `evaluate_briefs()` raises
  the existing `EvaluatorUnavailable` and every downstream consumer — the single-brief CLI's
  exit-2 branch and `cmd_evaluate()`'s per-group `groups_unevaluated` accounting — works
  unchanged. `SpawnExhausted` already subclasses `NoExecutionTarget`, so the one catch covers
  both shapes.
- **The single-brief CLI stops tracebacking on any selection failure.** The
  `--evaluate-brief-triage` branch gains a `NoExecutionTarget` catch that prints `null`,
  writes the documented `blocked_no_capacity:` line, and returns 2 — a backstop for the
  spawns in the pre-pass (repo inference) that are outside `evaluate_group()`'s scope.
- **No new operator procedure.** `skills/worktrail-go/SKILL.md:304-311` already documents
  exit 2 + `blocked_no_capacity:` as "no model ever evaluated the brief; do not apply; leave
  the brief queued". This change makes the code honour that text on the path where it
  currently does not.

## Capabilities

### Modified Capabilities

- `worker-exhaustion-non-result`: "Capacity exhaustion exits non-zero and distinguishably" is
  scoped to the condition rather than to one code path — a row with no live cell *on entry*
  produces the same exit-2/`blocked_no_capacity:` outcome as a row exhausted mid-spawn, and
  the batch command keeps the other groups' verdicts either way.

## Impact

- **Code**: `src/worktrail/workqueue/queue_triage.py` (`evaluate_group()`: one try/except and a
  failure-class read), `src/worktrail/router/skill_dispatch.py`
  (`--evaluate-brief-triage` branch: one catch).
- **Tests**: `tests/workqueue/test_triage_evaluator_exhaustion.py` and
  `tests/router/test_skill_dispatch_triage_capacity.py` (both already exist; new cases added).
- **Docs**: none — the contract is already documented at `skills/worktrail-go/SKILL.md:304-311`.
- **Non-goals**: changing when a cell is gated or how long a gate lasts; retrying automatically
  once capacity returns; the `exhausted`-flag plumbing in `spawnlib.py` (unchanged — this change
  adds no new give-up return); the other `spawn_agent()` call sites, which the existing
  call-site enforcement test already covers.
