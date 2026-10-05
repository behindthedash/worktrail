## Context

`prefer` reaches `select_cell()` from several surfaces: CLI flags (`worktrail-live`,
`worktrail-skill-dispatch --prefer`), role policy (`routing.roles.<role>.prefer`, validated at
load time), and caller constants (`retro.py`'s literal `"claude"`; `verify.py` and
`queue_triage.py` thread their `agent` parameter, default `"claude"`). `spawn_agent()`
threads the same value into `select_cell()` and, separately, into `_preflight_primary_target()`
-- a copy of the selector's steps 1-3 used only to decide whether the caller's primary-only
`extra_args` may be forwarded to the served cell.

Both copies open with the same three-clause guard (`prefer and prefer in order and prefer in
row`) and both silently no-op when it fails, so an unmatchable `prefer` never surfaces: not at
selection, not at dispatch, not in the run record. The brief
(`20261002-225026-unmatched-prefer-silently-ignored`) flags the fail-loud-vs-warn decision as
open; this document records the decisions taken and the contracts the change must not break.

## Goals / Non-Goals

**Goals:**

- A `prefer` that names no declared `routing.targets` entry fails loudly at the first
  resolution, before any spawn, naming the offending value and the declared targets.
- The two resolution sites can no longer disagree: both resolve through one validated
  function.
- Callers whose intent really is "best-effort preference for this harness" keep that behavior
  through a documented, testable adapter -- and retro's preference starts being honored
  instead of discarded.
- No change to the two pinned soft behaviors: a declared target absent from the chosen row
  remains a no-op, and capacity gating still degrades down the row.

**Non-Goals:**

- `worktrail-skill-dispatch --prefer`'s CLI presentation. A hand-typed undeclared value now
  raises (traceback) there; converting it to a `blocked_*` exit-2 line is a separate contract
  change, and no skill text passes `--prefer`. The error message is fully actionable.
- Drain's candidate-subset selection (`select_available_agent`). It already documents and
  tests its own "an unconfigured harness was never a real target to begin with" drop behavior
  and never passes a caller-supplied `prefer` through `select_cell()`.
- Load-time `routing.roles.<role>.prefer` validation. It stays warn-and-drop (the loader's
  house style for config-file entries); the runtime argument path is untouched by it.
- Changing what "absent from the row" means for a declared target.

## Decisions

### D1: An undeclared `prefer` raises; it does not warn or log

Alternatives considered:

- **Log the drop at the call site.** `select_cell()` is spec'd pure and deterministic with no
  log channel, so this would invent a side-effect parameter on the selector or bolt logging
  onto every caller -- and it fixes the diagnosis while leaving the outcome wrong: the spawn
  still serves a cell the caller did not ask for, which is the defect. The brief's own framing
  is "a run that looks configured but serves a different harness/model than asked".
- **Warn via the policy `_meta.warnings` channel** (the `roles.prefer` precedent). That is a
  *config-file* mechanism: dropping one bad entry keeps the rest of the file usable. A runtime
  `prefer` has no "rest" to keep -- it is a single explicit argument, and nothing consumes a
  warning on this path.
- **Raise.** Matches the module's own precedent for an explicit caller argument outside the
  supported catalog (`InvalidCandidate` for explicit provider/model overrides) and the spawn
  layer's precedent for an undeclared target (`explicit_cell_override`'s
  `OperatorConfigError`). It is also the direction PR #1390 took for the sibling
  silent-intent-drop defect: make the drop impossible, not merely visible.

### D2: Only the undeclared case raises; row-absent stays a soft no-op

The raise covers a `prefer` that is not a key of `routing.targets` at all -- a typo, a
renamed/dropped target, or a harness name where a target name belongs. A `prefer` that names a
*declared* target with no cell in the chosen row keeps today's behavior: no promotion, no
error, the row's own order decides.

The line between the two: a row-absent preference names something the operator did declare,
so the intent is coherent and the row is the documented fallback chain (the existing spec
scenario "Review degrades instead of failing when its preferred harness is gated" and
`test_prefer_naming_target_without_cell_in_row_is_a_no_op` pin exactly this). An undeclared
preference names nothing -- there is no reading under which falling through to the row's order
is what the caller asked for.

Rejected alternative: the brief's literal suggestion to raise when `prefer` is "neither a
declared entry nor present in the selected row" -- that would break two pinned contracts and
turn a supported degrade into a failure.

### D3: The error is `UndeclaredPrefer(SelectionError)` defined in `runtime/selection.py`

Not `OperatorConfigError`, which lives in `router/policy.py`: `selection.py`'s module contract
says the selector "deliberately knows nothing about subprocesses or configuration files", and
importing the policy module would break that layering. The same module already owns the
analogous error for an explicit caller argument outside the catalog (`InvalidCandidate`), and
`SelectionError` subclasses `ValueError` -- the family the repo's boundaries already treat as
operator-facing (`spawn_readiness.py` catches `ValueError` and notes "OperatorConfigError is a
ValueError"), while `live._crash_terminal_status` classifies non-`NoExecutionTarget` raises as
`failed` (not retryable), the right class for a name typo.

### D4: Harness hints are resolved by the caller, not by widening `prefer`

Alternatives considered:

- **Accept a bare harness name inside `select_cell`** (drain's `select_available_agent` and
  `live._first_target_for_harness` already map harness -> first declared target). Rejected:
  the mapping rule (first target in file order) is a policy-flavored choice the pure selector
  should not silently apply, and one string would get a routing-dependent meaning --
  `prefer="claude"` resolving on a routing that declares a claude target and raising on one
  that does not. It also would not save the caller work: the "no claude target declared" case
  still needs an explicit soft fallback, so `verify.py`/`queue_triage.py`/`retro.py` would
  need edits either way for their best-effort semantics.
- **Keep strict target-only and fix the callers via the shared mapper.** Chosen. `prefer`
  keeps one vocabulary; the soft intent is expressed where it is known (the caller holds a
  harness name), through `first_target_for_harness(routing, harness)` (pure) and
  `spawnlib.prefer_target_for_harness(harness)` (resolves routing fresh, the same per-call
  contract as `_default_model_for_agent`). `None` is the soft form: the row's own order
  decides.

This keeps `--prefer`'s CLI help ("target to move to the front of the resolved tier row")
accurate and retro's documented skip semantics intact: with no claude target declared, retro
resolves to `None` and still skips with `claude_harness_unavailable` rather than failing.

### D5: The two sites share `resolve_prefer()`; the ordering loops stay as they are

`select_cell()` and `_preflight_primary_target()` each call `resolve_prefer(routing, prefer)`
before their existing guard, so neither can silently drop an undeclared name and both agree by
construction on what the preference named. The rest of each loop is untouched: they differ in
output (cells vs names), and extracting the full ordering would be a larger refactor than the
defect warrants.

## Risks / Trade-offs

- [An out-of-repo caller passes a harness name as `prefer`] -> it now raises at the first
  spawn instead of quietly serving a different cell. The message names the offending value and
  the declared targets, and `prefer_target_for_harness` is the documented remedy.
- [A machine whose routing declares no claude target: retro's gate would raise if it passed
  the literal] -> handled by D4: retro resolves through the adapter, gets `None`, and keeps
  skipping with `claude_harness_unavailable` (pinned by test).
- [A declared target whose cell is missing from the row] -> unchanged soft no-op; the run
  record names the cell that actually served.
- [A future caller re-copies the guard instead of calling `resolve_prefer`] -> the shared
  function is the only validated path; the spec now states both sites use it, and the
  spawn-path test pins the raise reaching `spawn_agent`.

## Migration Plan

None: no persisted state, no config format change. Rollback is reverting the commit -- the
raise disappears and the callers' resolved preferences fall back to the literal no-op they had
before (the adapter calls would then pass target names that resolve normally, or `None`).
