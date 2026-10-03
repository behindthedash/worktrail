## Why

`prefer` is how a caller says "run this on target X". Both resolution sites -- `select_cell()`
(`src/worktrail/runtime/selection.py:371-373`) and `spawn_agent()`'s primary-target preflight
(`_preflight_primary_target()`, `src/worktrail/orchestrator/spawnlib.py:1126-1128`) -- carry
the identical guard `if prefer and prefer in order and prefer in row:` with no else branch and
no log. A `prefer` that names no declared `routing.targets` entry is therefore silently
ignored: selection falls through to the row's default order and nothing anywhere records that
the caller's intent was dropped. The run looks configured but serves a different harness/model
than asked -- the same silent-intent-drop family as the spawn-kwargs defect PR #1390 fixed.

It is live in-repo, not theoretical (work-queue brief
`20261002-225026-unmatched-prefer-silently-ignored`):

- `src/worktrail/learning/retro.py:89` and `:113` pass `prefer="claude"` -- a *harness* name --
  while `prefer` is a *target* name (the test fixtures' targets are
  `claude-sub`/`codex-sub`/`opencode-free`). The curation spawn's harness intent is silently
  discarded; only the `claude_harness_unavailable` check right after catches the mis-serve.
- A routing-config rename or drop turns a previously working `prefer` (from a CLI flag, a
  role, or a caller constant) into the same silent no-op, with no diagnostic anywhere.
- The same three-clause guard exists twice, so either site can drift from the other without a
  test noticing.

Two existing contracts bound the fix. `tests/runtime/test_selection.py:62`
(`test_prefer_naming_target_without_cell_in_row_is_a_no_op`) pins a `prefer` that names a
*declared* target with no cell in the chosen row as an intended soft no-op -- the row in file
order is the documented fallback chain. And the repo's other `prefer` precedent --
`routing.roles.<role>.prefer` validation in `policy.py`, pinned by
`tests/router/test_policy.py:1221 test_role_undeclared_prefer_dropped` -- is loader-style
warn-and-drop for a *config file* entry. Neither covers the runtime argument path, which is
where the silence lives.

## What Changes

- **An undeclared `prefer` fails loud.** `select_cell()` and `_preflight_primary_target()`
  both resolve `prefer` through one shared, validated resolution (`resolve_prefer()` in
  `runtime/selection.py`): an exact declared `routing.targets` name passes through; anything
  else raises a new `UndeclaredPrefer(SelectionError)` naming the offending value and the
  declared targets. `spawn_agent()` therefore raises before any subprocess starts, instead of
  silently serving the row's default order.
- **A declared target with no cell in the chosen row stays a soft no-op** -- unchanged and
  deliberately so: the pinned test and the spec's fallback-chain scenarios define that
  behavior. The raise is narrow, covering only names that exist nowhere.
- **A harness-level hint gets a supported form.** `runtime.selection.first_target_for_harness
  (routing, harness)` (first declared target with that harness, file order; `None` when none)
  plus the loader-level `spawnlib.prefer_target_for_harness(harness)` let a caller that holds
  a bare harness name express a best-effort preference without passing a name `prefer` can
  never match. The three in-repo callers that pass harness names today (`verify.py`,
  `queue_triage.py`, `retro.py`) resolve through it, so retro's "prefer claude, skip
  otherwise" semantics are preserved -- and its preference now actually reaches the claude
  target instead of being discarded.
- Specs and the routing-config operator docs state the new contract.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `model-tier-routing`: `prefer` becomes a validated target-name argument -- undeclared names
  raise before selection, the row-absent soft no-op is pinned explicitly, the primary-target
  preflight shares the resolution, and a documented harness-to-target adapter covers
  best-effort harness hints.
- `run-outcome-retro-agent`: retro's "preferring claude" is resolved to the first declared
  claude target (no preference when none is declared) rather than passing an unmatchable
  harness name.

## Impact

- `src/worktrail/runtime/selection.py` -- `UndeclaredPrefer`, `resolve_prefer()`,
  `first_target_for_harness()`, `select_cell()`'s resolution step.
- `src/worktrail/orchestrator/spawnlib.py` -- `_preflight_primary_target()` resolves through
  the same function; new `prefer_target_for_harness()`; `spawn_agent()` docstring.
- `src/worktrail/orchestrator/verify.py`, `src/worktrail/workqueue/queue_triage.py`,
  `src/worktrail/learning/retro.py` -- harness-name call sites resolve via
  `prefer_target_for_harness()`.
- `tests/runtime/test_selection.py`, `tests/orchestrator/test_spawnlib.py`,
  `tests/orchestrator/test_verify.py`, `tests/workqueue/test_queue_triage.py`,
  `tests/learning/test_retro.py` -- raise, adapter, and caller coverage.
- `skills/worktrail-routing-config/SKILL.md` + `references/gotchas.md` -- the operator-facing
  `prefer` contract.
