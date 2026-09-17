## 1. Free-form re-home consumption (`queue-triage`)

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, add a module-level re-home directive
      regex (verbs re-home/rehome/move/retarget/reassign, then `to`, optional `the`, a repo
      name token, optional `repo`) and extend `consume_repo_decision()`: when the question
      is not `REPO_ASSIGNMENT_QUESTION`, extract the directive's repo name from the answer
      and resolve it with `_resolve_repo_dir()`; return `None` when there is no directive
      or it does not resolve, otherwise stamp/overwrite `repo:`, append the note, and
      archive the decision exactly as the canonical path does. Make sure
      `_write_repo_inference()` replaces an existing `repo:` value. In the inventory loop,
      call `consume_repo_decision()` for repo-carrying briefs too and regroup on success;
      keep `unresolvable` reporting for the canonical question only. Update docstrings.
      (Requirement: Free-form repo re-home decisions are consumed.)
      In `tests/workqueue/test_queue_triage.py`, add tests for the three spec scenarios
      plus: canonical-question behaviour unchanged, and a repo-carrying brief is regrouped
      by the inventory in the same run.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate consume-freeform-repo-rehome-decisions --strict` and
      `worktrail-compile openspec/changes/consume-freeform-repo-rehome-decisions`.
