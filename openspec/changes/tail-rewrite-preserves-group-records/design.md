## Context

See `proposal.md` — Why, for the observed failure.

Two facts about the journal shape the approach:

- The journal is written by more than one writer across a run's phases. The pipeline
  scheduler's `_record()` owns `groups`; `_record_plan_fingerprint` owns
  `plan_fingerprint`/`plan_fingerprints`; `_mark_integrate_complete_if_terminal` owns
  `integrate_complete`. Each rebuilds or rewrites the file with `progress.atomic_write_text`.
- The codebase has already met this failure class once. `_preserve_plan_pin`
  (`src/worktrail/orchestrator/live.py:564`) exists because `live_run_real`'s and the
  scheduler's `record()` both rebuild their dict from scratch and one such rebuild silently
  wiped the plan pin (run `full-1786825958`). Its docstring states the design rule that
  matters here: carry the keys a *different* writer owns, explicitly enumerated — never a
  general merge of the on-disk journal, which would resurrect stale state the rebuilding
  writer intends to drop.

The tail phase is the second occurrence of exactly that class. `_dispatch_pending_tail` calls
`live_run_real(out_cassette=journal_path, resume=True)`; that invocation has no notion of
groups at all, so its rebuild carries only `PLAN_PIN_KEYS` and the pipeline phase's `groups`
map and `integrate_complete` marker are destroyed.

## Goals / Non-Goals

**Goals:**
- Make the carry-forward mechanism a first-class, declared-key concept rather than a
  single-purpose plan-pin special case, so the next second-writer is a one-line addition
  instead of a rediscovered bug.
- Restore the `groups`/`integrate_complete` durability the quarantined-group recovery path
  depends on.

**Non-Goals:**
- Changing the journal's on-disk format or key names.
- Making `worktrail-resume-group` (or any other reader/clearer) more defensive; the records
  are either present or the bug is fixed at the writer.
- Unifying journal writes behind a single lock or single writer process. That is a larger
  architectural change; the declared-key carry-forward is the narrow fix for this defect.
- Changing the pipeline scheduler's `_record()`: it is the *owner* of `groups` and already
  writes them; it has no second-writer problem to solve.

## Decisions

### Carry a declared key set, not a blanket merge

Generalize the existing helper into one function that takes the key tuple explicitly, and
declare two tuples: the existing pin keys and the cross-phase keys (`groups`,
`integrate_complete`). `live_run_real.record()` carries the union; the pipeline scheduler's
`_record()` keeps carrying the pin keys only.

*Alternative — add `groups`/`integrate_complete` to `PLAN_PIN_KEYS`:* rejected. The constant's
name and `_preserve_plan_pin`'s contract are about the plan pin; widening them would make both
names lie and hide the second-writer relationship. The generalized helper keeps the
"explicitly enumerated keys only" discipline that the existing docstring argues for.

*Alternative — merge the entire on-disk journal into the rebuilt dict:* rejected, for the
reason the existing docstring already gives: it resurrects state the rebuilding writer intends
to drop, and it would silently mask this class of bug instead of declaring the intended
carry-forward.

### Carry unconditionally in `live_run_real.record()`, not only under `with_tail`

`live_run_real` never writes `groups` or `integrate_complete` itself — on any path, it is
always the non-owner. Gating the carry on `with_tail=True` would leave the same destruction
reachable through the non-tail entry points (the `live-run-real` CLI at
`src/worktrail/orchestrator/live.py:7950`, and `--resume`), so the gate would be a second,
latent bug. Carrying unconditionally is both simpler and strictly more correct: it can only
add keys that (a) are already on disk and (b) the new dict does not set.

### Do not touch `_mark_integrate_complete_if_terminal`

The post-tail re-mark already reads `journal.get("groups", {})` and would set
`integrate_complete` correctly once the records survive; the only reason it cannot today is
that the map is gone. Its read-modify-write preserves whatever is on disk, so it needs no
change. Relying on it (rather than having the tail write stamp `integrate_complete` itself)
keeps terminality judged in one place.

## Risks / Trade-offs

- **A cleared group record could be resurrected by a later rewrite** → The carry only copies a
  key that is *present on disk and absent from the dict being written*. Every clearing path
  (`worktrail-resume-group`'s `clear_groups`, and `--re-integrate`'s
  `_clear_integrate_state`) writes the journal itself before any later phase runs, so a
  subsequent rebuild reads the already-cleared state and carries nothing back. The regression
  tests assert the post-clear direction as well as the preserve direction.
- **A stale map from a previous run's journal leaks into a new run** → Same guard: the key is
  only carried when it is on disk. A fresh run writes its own journal; a resumed run *should*
  inherit the previous run's group records, which is the point.
- **The tail phase's own group updates being masked** → `live_run_real` performs no group
  updates, so there is nothing of its own to mask; the pipeline phase remains the sole owner
  of the map's contents. The delta spec's fourth scenario pins this expectation.

## Migration Plan

No migration. The change is a behavior fix inside a running process; existing journals in the
field that already lost their `groups` map cannot be reconstructed by this change (the records
are gone), and are handled — if at all — by the separate `quarantine-recovery-command` work.
Rollback is a code revert; no on-disk state depends on the new behavior.
