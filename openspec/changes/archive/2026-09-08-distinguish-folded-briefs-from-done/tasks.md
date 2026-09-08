## 1. Superseded closure mode in `work_queue.py` (`folded-brief-superseded-status`)

- [x] 1.1 Implement requirements: Superseded closure mode stamps a distinct terminal
      status; Superseded closure is a non-shipping closure; Superseded briefs are
      terminal (the two `work_queue.py` sites).
      In `src/worktrail/workqueue/work_queue.py`: add a `superseded_by: str | None
      = None` keyword to `done()`; reject it combined with `planning_only` or
      `implementation_complete` (same shape as the existing
      "completion modes are mutually exclusive" error); include it in the
      `triage_closure` predicate so the Route-C gate does not fire; waive
      `_consolidation_closure_missing_evidence` for it exactly as
      `duplicate_of_stripped` already does; and when set, write
      `{"status": "superseded", "superseded-by": <id>, "completed-at": _now_iso()}`
      instead of `{"status": "done", ...}` in the `fields` dict, returning
      `{"status": "superseded", ...}` (design.md Decisions 1 and 2). Add
      `--superseded-by` to the `done` subparser's mutually exclusive mode group,
      thread it through the `args.cmd == "done"` dispatch, add `"superseded"` to the
      CLI success-status set (`work_queue.py:2197`) and keep the `done` command's
      `_BACKUP_ON` entry firing so a superseded closure is still backed up. Also
      treat `"superseded"` as terminal in `_related_still_open` (the
      `status == "done"` skip) and in the dependency-reference `state` computation
      (the `"done" if ... == "done" else "active"` expression).
      Add regression tests in `tests/workqueue/test_work_queue.py` asserting:
      (a) `done(id, superseded_by="X")` stamps `status: superseded`,
      `superseded-by: X` and `completed-at`, does not stamp `status: done`, and
      returns `{"status": "superseded"}`; (b) combining `superseded_by` with
      `planning_only` or `implementation_complete` is refused with no mutation;
      (c) a `recommended-route: C` brief closes cleanly with `superseded_by` alone
      (no `awaiting_implementation_decision`); (d) a brief carrying a
      `## Consolidated from` section closes with `superseded_by` and no note, while
      the same brief without `superseded_by` and without an evidencing note is
      still refused as `unverified_consolidation_closure`; (e) a related sibling
      stamped `status: superseded` is not reported in `related_still_open`, and a
      dependency reference to a picked brief stamped `status: superseded` resolves
      to the same state/`satisfied` values as one stamped `status: done`;
      (f) a plain `done(id, planning_only=True)` is unchanged — `status: done`, no
      `superseded-by:` field.

## 2. Consolidation closes absorbed members as superseded (`folded-brief-superseded-status`)

- [x] 2.1 Implement requirement: Cluster consolidation closes absorbed members as
      superseded. In `src/worktrail/router/consolidate_cluster.py`, rename
      `_mark_member_done` to `_mark_member_superseded` and give it a
      `new_brief_id` parameter; build its argv as
      `["done", member_id, "--superseded-by", new_brief_id, "--json"]` (plus the
      existing optional `--note`) instead of `["done", member_id,
      "--planning-only", "--json"]`, and treat `data.get("status") == "superseded"`
      as success. Update the call site in `execute_consolidation` to pass
      `new_brief_id`; leave `_stamp_superseded`, the nested-batch note built by
      `_build_nested_consolidation_note`, and the `members_completed`/
      `members_skipped` bookkeeping untouched (design.md Decision 3). Refresh the
      module docstring's description of the member-closure step so it no longer
      says members are marked done.
      Add regression tests in `tests/router/test_consolidate_cluster.py`
      asserting, against the existing `work_queue.py`-subprocess-driven fixtures:
      (a) after a confirmed `execute_consolidation`, each completed member's file
      carries `status: superseded` and `superseded-by:` naming the consolidated
      brief's id, carries no `status: done`, and still carries its `## Superseded`
      body note; (b) a member whose closure does not report `superseded` lands in
      `members_skipped`, not `members_completed`, and the batch continues;
      (c) a member that is itself a consolidation batch is closed with the
      nested-batch `--note` still attached.
      Depends on 1.1 for the `--superseded-by` flag it invokes.

## 3. Terminal-status treatment in the remaining brief consumers (`folded-brief-superseded-status`)

- [x] 3.1 Implement requirement: Superseded briefs are terminal (the non-`work_queue`
      sites). In `src/worktrail/workqueue/score_candidates.py`, extend the
      `cand_fm.get("status") == "done"` exclusion to also exclude `"superseded"`,
      and add a regression test in `tests/workqueue/test_score_candidates.py`
      (alongside the existing done-brief exclusion coverage) asserting a
      `status: superseded` candidate is excluded from scoring.
      No other `status == "done"` site changes here — `dashboard.py`'s `done`
      constants are task stages, not brief statuses (design.md Decision 2).
- [x] 3.2 In `src/worktrail/router/spec_sync_sweep_dedup.py`, extend the
      `fm.get("status") == "done"` skip in the `picked/` scan to also skip
      `"superseded"`, and add a regression test in
      `tests/router/test_spec_sync_sweep_dedup.py` asserting a `status: superseded`
      brief is not returned as an open brief covering a drift source.

## 4. Verification

- [x] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm it is green, including
      the new tests from sections 1-3.
- [x] 4.2 [e2e] Run `openspec validate distinguish-folded-briefs-from-done --strict`
      and confirm it passes.
