## 1. Re-scope verb in the re-home directive (`queue-triage`)

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, extend `_REHOME_DIRECTIVE_RE`'s verb
      alternation from `(?:re-?home|move|retarget|reassign)` to also accept `re-?scope`, and
      mention the new phrasing in the comment above it. No other part of the pattern or of
      `consume_repo_decision()` changes.
      (Requirement: Free-form repo re-home decisions are consumed.)
      In `tests/workqueue/test_queue_triage.py`, alongside the existing free-form re-home
      tests, add tests for the new spec scenarios: "Re-scope the brief's repo to worktrail"
      is consumed (repo rewritten, `repo-inferred` note, decision archived); unhyphenated
      "rescope it to the worktrail repo" is consumed; "Re-scope it to the nonesuch repo" leaves
      brief and decision untouched with no unresolvable entry.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate rehome-directive-rescope-verb-coverage --strict` and
      `worktrail-compile openspec/changes/rehome-directive-rescope-verb-coverage`.
