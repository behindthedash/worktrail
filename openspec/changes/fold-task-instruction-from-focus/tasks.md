## 1. Task instruction comes from the brief's focus

- [x] 1.1 Add `_fold_task_instruction(focus, evidence)` to
      `src/worktrail/workqueue/queue_triage.py`: collapse the focus to one line and
      return its first sentence, falling back to the collapsed evidence when the
      focus is empty. Split sentences on a `.`/`!`/`?` followed by whitespace and an
      opening capital, so a cited path (`qa-pipeline.yml:1709`) or version (`v5.0.0`)
      is never read as a sentence end.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
  files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

- [x] 1.2 [depends: 1.1] In `_apply_fold_into_change()`, resolve the brief with
      `_resolve_brief_path()`/`_brief_focus()` and emit the checklist item from
      `_fold_task_instruction()` instead of the collapsed `v.evidence`. Write the
      brief's focus and the evidence into `proposal.md`'s `## Folded from <brief-id>`
      section, and give the `tasks.md` group a one-line pointer to that section
      instead of the evidence prose.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
  files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

- [x] 1.3 [depends: 1.1] Widen `_fold_task_file_scope()` to take every text the task
      is built from (`*texts`) and pass the focus alongside the evidence, so a path
      named only in the focus still reaches the task's `files:` scope.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
  files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Tests

- [x] 2.1 [depends: 1.2, 1.3] In `tests/workqueue/test_queue_triage.py` give the fold
      fixture a realistic multi-sentence focus and cover: the checklist item is the
      focus's first sentence and the evidence appears nowhere in `tasks.md`; the group
      carries the `proposal.md` pointer; `proposal.md` carries both focus and evidence;
      a multi-line focus is collapsed to one line; a brief with no focus falls back to
      the collapsed evidence; the sentence split keeps a dotted path or version intact;
      and `_fold_task_file_scope()` picks up a path named only in the focus.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
  files: tests/workqueue/test_queue_triage.py

## 3. Verification

- [x] 3.1 [depends: 2.1] [e2e] Run `PYTHONPATH=src pytest -q --ignore=tests/orchestrator`,
      `ruff check src/ tests/`, `ruff format --check src/ tests/`, and
      `openspec validate fold-task-instruction-from-focus --strict`.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
  files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py
