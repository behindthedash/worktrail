## 1. Decision envelope and durable lifecycle

- [x] 1.1 Add the versioned pending-decision envelope, idempotent decision identity, provenance, answer-consumption, supersession, and run-record lifecycle helpers in `src/worktrail/workqueue/decisions.py` and `src/worktrail/router/run_record.py`; cover them in `tests/workqueue/test_decisions.py` and `tests/router/test_run_record.py`. (Requirement: Guards produce a stable decision envelope) (Requirement: Resume validates decision provenance and freshness)
  files: src/worktrail/workqueue/decisions.py, src/worktrail/router/run_record.py, tests/workqueue/test_decisions.py, tests/router/test_run_record.py

## 2. Guard and front-door boundary

- [ ] 2.1 Convert collision and related-brief guard outputs to the provider-neutral envelope in `src/worktrail/router/check_spec_collision.py` and `src/worktrail/router/check_related_brief_claims.py`, and their corresponding `tests/router/test_check_spec_collision.py` and `tests/router/test_check_related_brief_claims.py` suites.
  files: src/worktrail/router/check_spec_collision.py, src/worktrail/router/check_related_brief_claims.py, tests/router/test_check_spec_collision.py, tests/router/test_check_related_brief_claims.py
- [ ] 2.2 Implement attended host presentation and exact decision-ID resume at the go/adapter boundary in `src/worktrail/router/skill_dispatch.py` and `src/worktrail/router/poll_run.py`, with Claude, Codex, OpenCode, native, adapter, and subprocess cases in `tests/router/test_skill_dispatch.py`, `tests/router/test_poll_run.py`, and `tests/router/test_internal_dispatch_lifecycle.py`. (Requirement: Attended hosts present and resume the same contract)
  files: src/worktrail/router/skill_dispatch.py, src/worktrail/router/poll_run.py, tests/router/test_skill_dispatch.py, tests/router/test_poll_run.py, tests/router/test_internal_dispatch_lifecycle.py

## 3. Unattended ownership and orchestration gate

- [ ] 3.1 Make orchestrator dispatch reject unresolved decision envelopes and accept only provenance-validated resolved input in `src/worktrail/orchestrator/dispatch.py`, with contract tests in `tests/orchestrator/test_dispatch.py` and `tests/orchestrator/test_dispatch_extras.py`.
  files: src/worktrail/orchestrator/dispatch.py, tests/orchestrator/test_dispatch.py, tests/orchestrator/test_dispatch_extras.py
- [ ] 3.2 Teach drain to treat `pending_user_decision` as a fail-closed, recoverable handoff that neither guesses nor spins, updating `src/worktrail/drain/summary_contract.py`, `src/worktrail/drain/drain.py`, and `tests/drain/test_drain.py`. (Requirement: Unattended execution fails closed with a recoverable result)
  files: src/worktrail/drain/summary_contract.py, src/worktrail/drain/drain.py, tests/drain/test_drain.py

## 4. Procedure contract and provider matrix

- [ ] 4.1 Update the canonical guard/prompt/resume procedures in `skills/worktrail-go/SKILL.md`, `skills/worktrail-go/references/spec-collision-check.md`, `skills/worktrail-go/references/related-brief-collision-check.md`, `skills/worktrail-go/references/decision-queue.md`, and `skills/worktrail-go/references/subagent-prompts.md`; enforce the cross-surface contract in `tests/test_plugin_surface.py` without changing `openspec/specs/human-decision-queue/spec.md`. (Requirement: Decision lifecycle is auditable across dispatch modes)
  files: skills/worktrail-go/SKILL.md, skills/worktrail-go/references/spec-collision-check.md, skills/worktrail-go/references/related-brief-collision-check.md, skills/worktrail-go/references/decision-queue.md, skills/worktrail-go/references/subagent-prompts.md, tests/test_plugin_surface.py
- [x] 4.2 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, confirming the provider/dispatch-mode matrix and the existing human-decision-queue contract remain green.
