## 1. Expose the OpenSpec started-work signal without changing overlap candidates

- [ ] 1.1 In `src/worktrail/router/overlap_check.py`, add a focused reusable
      OpenSpec change helper that reads `tasks.md` and uses the existing
      top-level task parser to report whether at least one task is checked;
      treat absent or unreadable task lists and lists without recognized checked
      tasks as unstarted. Keep `scan()` and `_extract_openspec_change()`'s
      proposal-based active entries and five-key result shape unchanged. In
      `tests/router/test_overlap_check.py`, cover checked lowercase and uppercase
      task markers, unchecked-only and missing task lists, and prove an unstarted
      proposal remains an active scan result (Requirements: Started OpenSpec work
      is distinguished from a proposal; Unstarted proposals remain overlap
      candidates).
      files: src/worktrail/router/overlap_check.py tests/router/test_overlap_check.py

## 2. Apply the started-work signal to WIP-cap decisions

- [ ] 2.1 In `src/worktrail/workqueue/queue_triage.py`, make
      `_count_active_changes()` count only proposal-backed OpenSpec changes whose
      started-work helper reports true, without changing the cap's disabled,
      threshold, preview, apply-time recheck, or fold-candidate behavior. In
      `tests/workqueue/test_queue_triage.py`, update the active-change fixture as
      needed and add cap cases proving parked proposal-only repositories remain
      under cap while a repository with a checked task is held at the same cap
      (Requirement: WIP caps count only started OpenSpec work).
      files: src/worktrail/workqueue/queue_triage.py tests/workqueue/test_queue_triage.py

## 3. Verify the planned change

- [ ] 3.1 [e2e] Run `openspec validate count-started-openspec-wip --strict` and
      `worktrail-compile openspec/changes/count-started-openspec-wip`, confirming
      the change is valid and its task plan has no file-scope or requirement
      coverage violations. Verification-only, no file changes expected.
