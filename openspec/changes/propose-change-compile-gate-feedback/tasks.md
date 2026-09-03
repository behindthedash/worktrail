## 1. Prompt names both gates (`Fold and propose are applied as a pull request, fail-closed`)

- [ ] 1.1 Implement requirement: In `src/worktrail/workqueue/queue_triage.py`,
      extend `PROPOSE_CHANGE_PROMPT_TEMPLATE`'s trailing "must pass" sentence
      (design.md Decision) to also instruct the agent to run `worktrail-compile
      openspec/changes/{proposed_change_name}` and fix any reported problem —
      naming a same-file chain and a missing test-scope task as examples —
      before finishing, alongside the existing `openspec validate --strict`
      instruction. Add a regression test in `tests/workqueue/test_queue_triage.py`
      asserting `PROPOSE_CHANGE_PROMPT_TEMPLATE.format(...)` mentions
      `worktrail-compile` in addition to `openspec validate`, so the prompt
      text can't silently drop the compile-gate instruction again.

## 2. Verification

- [ ] 2.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm it is green,
      including the new test from section 1. Verification-only — no file
      changes expected.
- [ ] 2.2 [cleanup] Run `openspec validate propose-change-compile-gate-feedback
      --strict` and confirm it passes. Verification-only — no file changes
      expected.
