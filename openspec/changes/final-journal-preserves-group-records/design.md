## Context

See `proposal.md` -- Why, for the two journals and the writer inventory. Three properties of
the failure shape the approach:

- **The invariant is only observable at the end.** The cited run's journal was correct mid-tail
  (`groups` with `feature-1`, `integrate_complete: true`, 22:23) and wrong at rest (22:37). Any
  test that asserts on a snapshot taken while the run is in progress can pass while the bug is
  live, so the regression has to assert on the journal the run leaves behind.
- **The remaining writers are not symmetric.** `_carry_forward_keys` copies a declared key only
  when it is present on disk *and absent from the dict about to be written* (`live.py:596-599`).
  That exactly repairs `live_run_real.record()`, which never builds a `groups` key at all. It
  does **not** repair a writer that builds a `groups` map of its own and then writes it
  wholesale (`_pipeline_scheduler._record()`), because the key is present and the carry is a
  no-op -- a partially populated map is written over a fuller one.
- **The class is recurring.** The tail rewrite was the first fix of this shape; this is the
  second report. The predecessor's design already argued the discipline ("carry an explicitly
  declared key set, not a blanket merge") but nothing enforces it, so the next wholesale writer
  is a fourth bug report rather than a CI failure.

## Goals / Non-Goals

**Goals:**

- Name the writer that drops the records, from evidence, before changing any behavior.
- Make `groups` and `integrate_complete` survive from the write that creates them to the run's
  final journal, for the whole `full-real` sequence.
- Make a *future* wholesale journal writer unable to drop them silently.
- Pin all of it with a regression on the final journal plus a structural guard.

**Non-Goals:**

- Reconstructing records already lost in journals in the field (nothing on disk can rebuild
  them); that is the separate `quarantine-recovery-command` work.
- Making readers defensive. `worktrail-resume-group` reporting `no record in journal` is
  correct behavior for a journal that is missing the record; the writer is the defect.
- Making `worktrail-resume-group` or `--re-integrate` stop clearing records. Clearing is the
  point of both; only *incidental* drops are the bug.
- Unifying journal writes behind one writer, one lock, or one process. That is a larger
  architectural change; the declared-key carry-forward (widened where needed) is the narrow fix.
- Changing the journal format, key names, or the tail fix's existing behavior.

## Decisions

### Attribute from evidence, then fix; do not fix by inspection

The brief's fix direction is an attribution, and it is the right one: there are two candidate
writers and their failure signatures differ only in detail, so a fix applied by inspection may
leave the real writer untouched and the bug live. The attribution runs as part of this change
and is expected to name the writer; the concrete method:

1. **Reproduce end to end** in a throwaway repo whose spec quarantines one pipeline group after
   its PR is merged -- the harness `tests/orchestrator/test_pipeline_e2e.py` already drives
   `live._pipeline_scheduler(...)` with an injected fake spawn and verifier, and already has a
   `test_tail_task_dispatched_after_all_groups_merged` case, so the tail and post-tail phases are
   reachable without a real `claude -p`, `gh`, or CI.
2. **Instrument the write, not the callers.** Wrap the run journal's `progress.atomic_write_text`
   so each write logs its caller and the `groups` / `integrate_complete` key set before and
   after (a diagnostic hook, gated so it does not add output to ordinary runs). A write whose
   *after* set is smaller than its *before* set is the drop, whatever called it.
3. **Bisect against the run's timeline**: the journal's mtime against the orchestrator log's own
   phase timestamps, per the brief, as the independent check on the stack the instrumentation
   captured.

The finding is recorded in this design's Decisions when the task lands. If attribution shows a
writer outside the two candidates, the fix below moves to that writer unchanged in shape.

### The fix is the declared-key carry-forward, widened where the writer needs it

Whichever writer the attribution names, the repair stays inside the existing mechanism rather
than inventing a second one:

- A writer that builds **no** `groups` key of its own (`live_run_real.record()`'s shape) is
  already handled by `_carry_forward_keys(path, jdict, PLAN_PIN_KEYS + PIPELINE_PHASE_KEYS)`;
  if it is somehow re-reached without that call, the fix is to restore the call, not to add a
  new helper.
- A writer that builds a **partial** `groups` map (`_pipeline_scheduler._record()`'s shape)
  cannot be repaired by the carry as written, because the key is present. There the write merges
  the on-disk records into the writer's map before writing -- on-disk records this invocation did
  not itself write are added, records it did write win -- and carries `integrate_complete` when
  it did not set it. This keeps the writer's own updates authoritative while making the map it
  writes a superset of what is on disk, which is what "the run's records survive" means.

*Alternative -- add `groups`/`integrate_complete` to every rebuild site by hand each time:* that
is the current state of affairs, and it has now produced two bugs.

*Alternative -- merge the whole on-disk journal into every rebuild:* rejected for the reason the
existing helper's docstring already gives -- it resurrects state the rebuilding writer intends
to drop. The merge above is deliberately narrower: single keys, and only records the writing
invocation did not itself author.

*Alternative -- make `atomic_write_text` carry the keys implicitly:* rejected. `atomic_write_text`
has no idea which file it is writing, and the read-modify-write writers (the majority of calls)
would then be doing a redundant merge on every write, including the deliberate clearing paths.

### A structural guard, not a fourth bug report

`tests/orchestrator/test_quarantine_write_sites_structural.py` already establishes the pattern in
this repo: statically enumerate every call to a journal-write primitive and assert the property
that must hold at each one, with a meta-test proving the scanner is not vacuously passing. The
same scanner is added for the rebuild-writer property: every call site that rebuilds the run
journal from a fresh dict literal (`spec_id`/`entries`/`gitnexus_capability`) is asserted to
carry the cross-phase keys, so a new wholesale writer fails CI. Read-modify-write sites are
recognized by their load-then-extend shape and excluded, matching how the existing writer
inventory was compiled by hand for this change.

### Assert on the final journal, end to end

The regression drives the whole `full-real` sequence -- pipeline fan-out through tail dispatch
through the post-tail writes -- and asserts on the journal after it returns: the quarantined
group's record is still present and `worktrail-resume-group` (or the same clearing path) names
it. A unit test on one writer's `record()` is not sufficient evidence and is exactly the kind of
intermediate-snapshot test that let this survive the tail fix.

## Risks / Trade-offs

- **The end-to-end harness may not reproduce the field loss** (a process boundary or a resumed
  invocation may be required) → the attribution step reports which; the merge/carry fix and the
  structural guard are still correct and still pinned, and the harness keeps whatever part of the
  sequence it can drive. The task is not considered done until it either reproduces the drop or
  states, in the design, which writer was named and by what other evidence.
- **A merge could resurrect a deliberately cleared record** → the merge only adds records the
  writing invocation did not author, and every clearing path (`resume_group.clear_groups`,
  `--re-integrate`'s `_clear_integration_state`) writes the journal itself before any later
  writer runs, so a later rebuild reads the already-cleared state. The delta spec pins the
  direction with an explicit "an explicit clear still removes the record" scenario.
- **Patient zero is not fixed** → the two journals cited cannot be repaired; the change fixes the
  writer that will drop records in the next run, which is the only fixable half.
- **The structural guard could be too strict and block a legitimate writer** → the guard asserts
  a property every correct writer already satisfies (the tail writer does today), and a writer
  that genuinely must not carry them declares its exclusion in the same place its shape is
  recognized -- the same escape the quarantine-write-sites guard uses for
  `_persist_newly_quarantined`.

## Migration Plan

None. The change is a behavior fix inside a running process plus tests; no configuration, journal
schema, or data migration, and no operator-visible interface changes. Rollback is a code revert;
journals already damaged in the field stay damaged either way, and are the
`quarantine-recovery-command` work's concern.
