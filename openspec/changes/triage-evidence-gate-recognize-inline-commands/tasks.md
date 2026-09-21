## 1. Accept inline interpreter reproductions and resolve bare basenames

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, add an alternative to
      `_REPRODUCTION_EVIDENCE_RE` matching an inline interpreter invocation -- `python`,
      `python3`, or `py` followed by whitespace and a `-c` or `-m` flag -- and extend the
      comment block above the pattern to record why the flag is required (the bare interpreter
      name appears in prose, exactly as `grep`/`git` do). Leave `_work_directly_accepted` and
      `_work_directly_downgrade_note` untouched; the gate's structure does not change.
      (Requirements: An inline interpreter reproduction counts as reproduction evidence.)
      In `src/worktrail/workqueue/premise_check.py`, give `_check_path` a basename fallback:
      when the candidate contains no `/` and `repo_path / candidate` does not exist, run
      `git ls-files` in the checkout and match tracked paths whose final segment equals the
      candidate; one or more matches confirm, with a detail naming the resolved path (or the
      match count plus one example), and no match falls through to the existing
      `path does not exist` outcome. Keep the fallback scoped to the presence branch and to
      candidates with no `/`, and note the rule in the module docstring.
      (Requirements: A bare-basename path needle resolves anywhere in the checkout.)
      Extend `tests/workqueue/test_queue_triage.py` with evidence-gate cases (an inline
      `python3 -c` reproduction accepted, `python -m ...` accepted, bare prose "python" still
      downgraded, and preview agreeing with apply) and
      `tests/workqueue/test_premise_check.py` with basename cases against the file's existing
      temporary-repo fixture (unique basename confirms with its resolved path, duplicated
      basename confirms with a count, unknown basename still refutes, and a rooted `path:line`
      needle keeps its current detail).
      files: src/worktrail/workqueue/queue_triage.py, src/worktrail/workqueue/premise_check.py, tests/workqueue/test_queue_triage.py, tests/workqueue/test_premise_check.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py
      tests/workqueue/test_premise_check.py`, then `PYTHONPATH=src pytest -q`, `PYTHONPATH=src
      python3 -m worktrail.orchestrator.orchestrate check`, and `python3
      scripts/ci/ruff_pinned.py check .`. Run `openspec validate
      triage-evidence-gate-recognize-inline-commands --strict` and `worktrail-compile
      openspec/changes/triage-evidence-gate-recognize-inline-commands`.
