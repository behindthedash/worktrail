## 1. Consume the proceed-as-scoped answer (`queue-triage`)

- [ ] 1.1 In `src/worktrail/workqueue/queue_triage.py`: in `consume_repo_decision()`, before
      reporting a canonical answer as unresolvable, compare it (casefolded, stripped, trailing
      `.` removed) against `_NEEDS_DECISION_OPTIONS[0]`; on a match stamp
      `repo-less: confirmed` via `_set_fm_fields()`, append a `## Triage <date>` note with
      `verdict: repo-less-confirmed` / `rule: decision`, call `decisions.resolve_decision()`,
      and return `{"resolved": False, "confirmed_repo_less": True, "path", "decision_id"}`.
      In `group_queue_by_repo()`, do not append such an outcome to `unresolvable` (the brief
      stays under `NO_REPO_KEY`). In the escalation path (`escalation_due()` / the repo-less
      branch of the escalation matrix), treat a repo-less brief whose frontmatter has
      `repo-less: confirmed` as not due, so `REPO_ASSIGNMENT_QUESTION` is never re-issued.
      Update the affected docstrings.
      (Requirement: Proceed-as-scoped answer confirms a repo-less brief.)
      In `tests/workqueue/test_queue_triage.py`, alongside the existing
      `consume_repo_decision` and escalation tests, add one test per spec scenario: consumed
      verbatim answer; lenient case/period match; other unresolvable answer still reported
      and untouched; confirmed brief not escalated / no decision filed; unconfirmed brief
      still escalates.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate queue-triage-consume-proceed-as-scoped-decision --strict` and
      `worktrail-compile openspec/changes/queue-triage-consume-proceed-as-scoped-decision`.
