## Context

`_delta_precheck(worktree, change_id, *, allow_delta_drift, timeout)` in
`src/worktrail/router/close_stale_openspec.py` is already a pure, read-only
function returning `(precheck_dict, error_or_None)`. It takes the worktree
root and change id, runs `openspec validate --strict`, walks the delta files
against `openspec/specs/`, and calls `dashboard._openspec_delta_drift`. Drain's
`_run_openspec_archive(wt, spec_id, timeout)` has exactly those inputs in hand.

## Decisions

### D1: Reuse `_delta_precheck`, do not duplicate it

Drain imports `_delta_precheck` alongside the `flip_and_archive` import it
already has from the same module. The heading grammar, the three refusal
classes, and the error wording stay defined once. The leading underscore is a
module-privacy convention within the package, not a contract boundary; drain
already imports `flip_and_archive` from this module and the two are in the
same package.

### D2: Pre-check runs after the unchecked-task refusal, before archive

Order inside `_run_openspec_archive`: (1) existing unchecked-task refusal
(cheap, needs no subprocess), (2) `_delta_precheck`, (3) `openspec archive -y`.
A pre-check error raises `RuntimeError(f"refusing to archive {spec_id} (in
{wt}): {error}")`, matching the existing refusal's message shape. Because the
raise happens before `openspec archive`, nothing has been written to the
worktree; the sweep engine's per-finding try/except logs the message and moves
on, and `_reset_stale_bookkeeping_worktree` rebuilds the branch from base on
the next sweep exactly as it does for any other mid-flight failure.

### D3: No drift override in drain

`_delta_precheck` is called with `allow_delta_drift=False`, unconditionally.
The override exists so an interactive agent that has just reconciled a delta
in an uncommitted worktree can proceed; drain's worktree is freshly created
from base every time, so that situation cannot arise, and an unattended sweep
has no operator to make the judgment call. A drifted delta is refused every
sweep and surfaces in the log until it is fixed through the interactive path.
The stuck-remediation detector does not see it (it only counts findings whose
action did not raise), which is correct: the error line is already visible.

### D4: Test fakes

`_fake_gh_and_openspec_archive_subprocess_run` in `tests/drain/test_drain.py`
gains an `openspec validate` branch returning exit 0 so the existing archive
tests keep passing without a real CLI. `dashboard._openspec_delta_drift` is
patched to return `[]` in the pass-through case, and to return a finding in
the drift-refusal test, mirroring how the close-stale tests exercise it.

## Alternatives considered

- **Call `flip_and_archive` from drain instead.** Rejected: it also flips
  checkboxes and drives `--json` archive output; drain's shape assumes the
  change is already fully checked and refuses otherwise (per the existing
  requirement). Reusing only the pre-check keeps that refusal intact.
- **Add `--allow-delta-drift` to `worktrail-drain`.** Rejected per D3.
