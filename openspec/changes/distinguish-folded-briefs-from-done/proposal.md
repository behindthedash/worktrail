## Why

`consolidate_cluster.py`'s `execute_consolidation()` closes every brief it
absorbs into a consolidated wrapper by calling `work_queue.py done <id>
--planning-only` (`_mark_member_done`, `consolidate_cluster.py:584-602`) and
appending a bare `## Superseded` footer (`_stamp_superseded`,
`consolidate_cluster.py:605-618`). The result on disk is indistinguishable
from a brief whose work actually shipped: `status: done` plus a
`completed-at` stamp, with no shipping evidence of any kind required.

This is the exact asymmetry `done()`'s own
`_consolidation_closure_missing_evidence` gate
(`work_queue.py:242-263`) was built to close — that gate refuses to let the
*wrapper* brief close without per-sub-item evidence, precisely because
"`execute_consolidation()` already stamps each sub-item `done` at
batch-*authoring* time (before any of the described work exists to cite)".
Nothing stops the fold step from making that stamp; the gate only makes it
harder to close the wrapper afterwards, leaving the absorbed sources
permanently mislabelled as completed work.

Live evidence (brief `20260907-160003-fold-marks-source-briefs-done`): 11 of
the 15 members of datalena batch `20260813-113653` each carry `status:
done`, `completed-at` stamps 2-70 seconds apart, and a bare `##
Superseded -> Consolidated into ...` footer — including four large
Epic-004/Ask-Lena feature deltas with no PR, test, or diff evidence that any
of the described work shipped. `done` is the queue's "this work is finished"
signal; a fold is "this work now lives somewhere else", which is a different
claim and must be stamped differently.

## What Changes

- `work_queue.py`'s `done()` gains a `superseded_by` closure mode
  (`--superseded-by <brief-id>` on the CLI): it stamps `status: superseded`,
  `superseded-by: <brief-id>` and `completed-at` instead of `status: done`,
  and returns `{"status": "superseded", ...}`. Like the existing
  `duplicate_of` closure it is a non-shipping closure — it satisfies the
  Route-C continue-vs-planning-only gate and waives the
  consolidation-evidence gate, because a folded brief's work is *carried* by
  the surviving brief, not shipped by this one.
- `superseded` is a terminal status everywhere the work queue currently
  treats `done` as terminal for briefs, so a folded brief is never
  resurfaced as open work: `_related_still_open`, the dependency-reference
  state resolution in `work_queue.py`, `score_candidates.py`'s
  done-brief exclusion, and `spec_sync_sweep_dedup.py`'s open-brief lookup.
- `consolidate_cluster.py`'s `execute_consolidation()` closes each absorbed
  member through the new mode (`--superseded-by <new_brief_id>`) instead of
  `done --planning-only`, so the fold no longer claims the member's work
  shipped. The `## Superseded` body footer and the batch-provenance closure
  note are unchanged.
- Regression coverage for the new closure mode, its gate waivers, the
  terminal-status treatment of `superseded`, and the fold path's use of it.

## Capabilities

### New Capabilities
- `folded-brief-superseded-status`: a distinct, terminal `superseded`
  closure for a brief whose text was absorbed into another brief, separate
  from the `done` closure that asserts the work itself finished; and the
  requirement that the fold/consolidation path use it.

### Modified Capabilities
(none — no existing spec states that a folded member is closed as `done`;
this adds a new closure mode and a new terminal status alongside the
existing ones)

## Impact

- `src/worktrail/workqueue/work_queue.py` (`done()`, its CLI `done`
  subparser and dispatch, `_BACKUP_ON`/exit-status mapping,
  `_related_still_open`, the dependency-reference `state` computation)
- `src/worktrail/router/consolidate_cluster.py` (`_mark_member_done` and its
  call site in `execute_consolidation`)
- `src/worktrail/workqueue/score_candidates.py` (done-brief exclusion)
- `src/worktrail/router/spec_sync_sweep_dedup.py` (open-brief lookup)
- `tests/workqueue/test_work_queue.py`,
  `tests/workqueue/test_score_candidates.py`,
  `tests/router/test_consolidate_cluster.py`,
  `tests/router/test_check_spec_sync.py`
- Out of scope: auditing the 11 already-mislabelled datalena/Epic-004 briefs
  named in the source brief. That is a different repo's corpus and needs its
  own brief against `datalena`; this change only stops new folds from
  producing the same ambiguity. Existing `status: done` briefs are not
  migrated or rewritten.
