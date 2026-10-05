## 1. Attribute the writer and carry the records to the final journal

- [ ] 1.1 Attribute the post-tail write that drops the pipeline phase's `groups` records, then
      make that writer carry them forward. Attribute first, from evidence: reproduce the loss
      with the existing `full-real` harness (a throwaway repo whose spec quarantines one
      pipeline group, driven through `live._pipeline_scheduler(...)` with an injected fake
      spawn and verifier, as in `tests/orchestrator/test_pipeline_e2e.py`), instrument the run
      journal's write path so each write logs its caller and the `groups`/`integrate_complete`
      key set before and after (gated so ordinary runs are unaffected -- a write whose after-set
      is smaller than its before-set is the drop), and bisect the journal mtime against the
      orchestrator log's phase timestamps as the independent check; a writer other than the two
      candidates named in `proposal.md` is a valid finding. Then apply the fix in the existing
      mechanism: for a writer that builds no `groups` key, restore the
      `_carry_forward_keys(path, jdict, PLAN_PIN_KEYS + PIPELINE_PHASE_KEYS)` call at its write
      (`live.py:4963` is the tail writer's, already correct); for a writer that builds a
      `groups` map of its own (`_pipeline_scheduler._record()`, `live.py:6370`, which writes its
      in-memory `groups_journal` only and is seeded from disk only under `resume`, `live.py:6333`),
      merge the on-disk records it did not itself author into that map before writing -- its own
      records for the groups it touched winning -- and carry `integrate_complete` when it did not
      set it. A pure absent-key carry-forward cannot repair that writer: the key is present, so
      the carry is a no-op and a partial map is written over a fuller one. Do not widen the
      merge to whole-journal state, do not change the journal's format, and do not touch
      `worktrail-resume-group` or `--re-integrate`. Record the attributed writer, the evidence
      that named it, and any instrumentation left behind in `design.md`'s Decisions.
      (Requirement: A run's group records survive a later phase's journal rewrite)
      Add `tests/orchestrator/test_journal_carry_forward.py` covering the attributed writer at
      unit level against a real journal file: a partial in-memory `groups` map plus the on-disk
      record it does not know about yields a written map containing both, `integrate_complete`
      survives a rebuild that does not set it, the writer's own update for a group it did touch
      wins over the on-disk one, a record cleared from disk before the write is not resurrected,
      and an absent or unreadable journal leaves the rebuild's own keys intact rather than
      raising.
      files: src/worktrail/orchestrator/live.py src/worktrail/orchestrator/integrate.py src/worktrail/orchestrator/progress.py tests/orchestrator/test_journal_carry_forward.py

- [ ] 1.2 Add the end-to-end regression the brief specifies: quarantine a pipeline group, run
      the whole `full-real` sequence, and assert the record survives to the FINAL journal. Drive
      `live.full_real(...)` (not a single phase) against a scratch git repo whose spec's group
      quarantines after its PR merges, with an injected fake spawn plus fake verifier so no real
      `claude -p`, `gh`, or CI is needed, and the tail dispatch plus post-tail reconciliation and
      checkbox writes all run. Assert on the journal after the call returns, not on any snapshot
      taken while it runs -- the cited run's mid-tail snapshot was correct while its end state
      was not; assert the quarantined group's QUARANTINED record is present, that a MERGED
      record from the same phase is present, and that `integrate_complete` was not erased, and
      assert `worktrail-resume-group`'s selection path names the quarantined group rather than
      failing with `no record in journal`. The test must fail against the pre-fix writer (state
      that it does, and on what), and is the change's proof the drop is gone end to end.
      (Requirement: A run's group records survive a later phase's journal rewrite)
      files: tests/orchestrator/test_full_real_final_journal_group_survival.py
      depends: 1.1

## 2. Guard the journal-rebuild write sites against the next writer

- [ ] 2.1 Add a structural AST guard in the shape of
      `tests/orchestrator/test_quarantine_write_sites_structural.py`: statically enumerate every
      journal-write call site in `live.py` and `integrate.py`, classify each as a wholesale
      rebuild (a fresh dict literal carrying `spec_id`/`entries`/`gitnexus_capability`) or a
      read-modify-write, and fail when a rebuild site does not carry the declared cross-phase
      keys (`PIPELINE_PHASE_KEYS`, or the merge introduced by 1.1) before its write. Include the
      meta-test proving the scanner is not vacuously passing -- feed it a synthetic rebuild site
      with the carry removed and assert it is reported -- and a recognized-exclusion list for
      the write sites whose shape is legitimately not a rebuild (the cassette/demo `live_run`,
      `progress.set_phase`'s sidecar), so a future writer that must not carry the keys declares
      its exclusion where its shape is recognized rather than silently passing.
      (Requirement: A run's group records survive a later phase's journal rewrite)
      files: tests/orchestrator/test_journal_rebuild_carry_forward_structural.py
      depends: 1.1

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/orchestrator/test_journal_carry_forward.py
      tests/orchestrator/test_full_real_final_journal_group_survival.py
      tests/orchestrator/test_journal_rebuild_carry_forward_structural.py`, then the full
      `PYTHONPATH=src python3.14 -m pytest -q` and `PYTHONPATH=src python3.14 -m
      worktrail.orchestrator.orchestrate check`, then `python3.14 scripts/ci/ruff_pinned.py
      check .`, `python3.14 scripts/ci/ruff_pinned.py format --check .` and `python3.14
      scripts/ci/check_shebang_exec_bits.py`. Confirm the end-to-end regression fails when
      reverted to the pre-fix writer and passes with it, and exercise the sequence once by hand
      in a scratch repo: quarantine a group in the pipeline phase, let the run reach its tail
      phase and complete, and confirm the final journal still carries the group's QUARANTINED
      record and `integrate_complete`, and that `worktrail-resume-group` names and clears it.
      Run `openspec validate final-journal-preserves-group-records --strict` and
      `worktrail-compile openspec/changes/final-journal-preserves-group-records`.
      depends: 1.1, 1.2, 2.1
