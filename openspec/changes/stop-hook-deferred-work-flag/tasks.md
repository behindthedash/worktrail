## 1. New `check_deferred_work_handoff.py` module

- [ ] 1.1 Create `src/worktrail/router/check_deferred_work_handoff.py`: load one or more
      run-record YAML paths and read only their `deferred_work` list, never `scope_review`
      (Requirement: Deferred-Work-Only Signal Source)
- [ ] 1.2 Add a `DEFERRAL_PHRASES` module-level constant and a case-insensitive
      substring-match function over `deferred_work` entry text (Requirement: Deferral-Phrase
      Matching)
- [x] 1.3 Implement the handoff cross-check: import `extract_probes` from
      `worktrail.router.check_brief_staleness`, run it against each phrase-matching entry,
      and search `queue/*.md` + `picked/*.md` (via `work_queue.queue_dir()` /
      `work_queue.picked_dir()`) focus text for a probe substring match; unreadable/missing
      queue directories or unparseable briefs are skipped, never treated as a match
      (Requirement: Handoff Cross-Check Before Flagging)
- [ ] 1.4 Implement the CLI `main()`: repeatable `--run-record PATH`, `--json`, printing
      `{"flagged": [{"text": ..., "run_record": ...}]}`; malformed/unreadable YAML for one
      run record is skipped and never raises (Requirement: Fail-Open And Headless-Excluded)
- [ ] 1.5 Register `worktrail-check-deferred-work-handoff =
      "worktrail.router.check_deferred_work_handoff:main"` in `pyproject.toml`'s
      `[project.scripts]`

## 2. Hook integration

- [x] 2.1 Extend `hooks/suggest_next_step.py`'s existing transcript line-loop
      (`entry_has_work`/`substantive_work`) to also collect unique
      `~/.worktrail/runs/**/*.yaml`-shaped path literals from the same read pass, without a
      second file read (Requirement: Run-Record Discovery Via Transcript Grep)
- [ ] 2.2 After the existing sentinel/substantive-work gate, locate
      `worktrail-check-deferred-work-handoff` via `shutil.which` and invoke it as a bounded-
      timeout subprocess with the discovered run-record path(s); a missing binary, non-zero
      exit, timeout, or unparseable JSON is treated identically to "nothing flagged"
      (Requirement: Fail-Open And Headless-Excluded)
- [ ] 2.3 When the subprocess reports at least one flagged entry, append a second,
      separate instruction block to the same `reason` string, leaving the `INSTRUCTION`
      constant's text and the EXCEPTIONAL-VALUE gate's own trigger conditions unmodified;
      when nothing is flagged, `reason` is exactly what it is today (Requirement: Additive
      And Non-Interfering) (Requirement: Silent When Nothing Unmatched)

## 3. Tests

- [ ] 3.1 `tests/router/test_check_deferred_work_handoff.py`: a run record's
      `deferred_work` entries are read and matched; its `scope_review` entries (including
      an `out-of-scope | ... | different purpose: ...` entry) are never read or matched,
      even when they contain deferral-phrase vocabulary (Requirement: Deferred-Work-Only
      Signal Source)
- [ ] 3.2 Same file: phrase-matching entries become candidates; non-matching entries are
      never flagged regardless of handoff coverage (Requirement: Deferral-Phrase Matching)
- [ ] 3.3 Same file: a candidate whose extracted probes match an existing `queue/` or
      `picked/` brief's focus text is not flagged; a candidate matching no brief is
      flagged; an unreadable/missing work-queue directory yields "not flagged," never an
      exception (Requirement: Handoff Cross-Check Before Flagging)
- [ ] 3.4 `hooks/test_suggest_next_step.py`: a transcript containing a run-record path
      literal whose `deferred_work` is empty or fully phrase-non-matching produces output
      byte-for-byte identical to today's EXCEPTIONAL-VALUE-only instruction (Requirement:
      Additive And Non-Interfering) (Requirement: Silent When Nothing Unmatched)
- [ ] 3.5 Same file: a transcript with a run-record path literal whose `deferred_work` has
      an unmatched, phrase-matching entry produces output containing both the unmodified
      EXCEPTIONAL-VALUE instruction and the new deferral-flag block (Requirement: Additive
      And Non-Interfering)
- [ ] 3.6 Same file: a transcript with no run-record path literal, a missing
      `worktrail-check-deferred-work-handoff` binary, and `CC_HEADLESS=1` each leave the
      hook's behavior unchanged/fail-open (Requirement: Run-Record Discovery Via Transcript
      Grep) (Requirement: Fail-Open And Headless-Excluded)

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; confirm both are green.
