## 1. Preserve the pipeline phase's journal records across the tail rewrite

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, make the journal carry-forward a
      declared-key operation: one helper that copies an explicitly passed key tuple from the
      journal on disk into the dict about to be written, used by both existing callers, with
      `PLAN_PIN_KEYS` kept as-is and a new declared tuple for the keys the pipeline phase owns
      (`groups`, `integrate_complete`). `live_run_real`'s `record()` — the tail phase's writer,
      reached from `_dispatch_pending_tail`'s `out_cassette=journal_path` call — carries the
      union, so its rebuild no longer erases the quarantined/missing group records the pipeline
      phase wrote. Keep `_preserve_plan_pin`'s name and pin-only contract intact for its
      existing callers, and keep the pipeline scheduler's `_record()` carrying the pin keys
      only (it owns `groups` and writes them itself). Do not touch
      `_mark_integrate_complete_if_terminal`: it reads `journal["groups"]` and needs no change
      once the records survive.
      (Requirement: A run's group records survive a later phase's journal rewrite)
      Add `tests/orchestrator/test_tail_journal_group_survival.py` proving, against a real
      journal file, that: a QUARANTINED group record plus `integrate_complete` survive the
      rewrite a `live_run_real`-shaped rebuild performs; a MERGED record survives the same
      rewrite; a record already cleared from disk (the `worktrail-resume-group` /
      `--re-integrate` direction) is not resurrected by a later rewrite; and an absent or
      unreadable journal leaves the rebuild's own keys intact rather than raising. Extend the
      existing wholesale-rewrite coverage in `tests/orchestrator/test_plan_fingerprint_record.py`
      to assert the pin keys still survive through the generalized helper, so the
      refactor is pinned by the tests that already guard that mechanism.
      files: src/worktrail/orchestrator/live.py tests/orchestrator/test_tail_journal_group_survival.py tests/orchestrator/test_plan_fingerprint_record.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/orchestrator/test_tail_journal_group_survival.py
      tests/orchestrator/test_plan_fingerprint_record.py
      tests/orchestrator/test_quarantine_journal_persistence.py
      tests/orchestrator/test_resume_group.py tests/orchestrator/test_integrate_complete.py`,
      then the full `PYTHONPATH=src python3.14 -m pytest -q` and `PYTHONPATH=src python3.14 -m
      worktrail.orchestrator.orchestrate check`, then `python3.14 scripts/ci/ruff_pinned.py
      check .`, `python3.14 scripts/ci/ruff_pinned.py format --check .` and
      `python3.14 scripts/ci/check_shebang_exec_bits.py`. Exercise the real sequence end to end
      in a scratch repo whose journal records a QUARANTINED group: drive the tail phase's
      `live_run_real(..., out_cassette=<journal>, resume=True)` path and confirm the completed
      journal still carries that group's QUARANTINED record and `integrate_complete`, and that
      `worktrail-resume-group` then names and clears it instead of failing with
      `no record in journal`. Run `openspec validate tail-rewrite-preserves-group-records
      --strict` and `worktrail-compile openspec/changes/tail-rewrite-preserves-group-records`.
