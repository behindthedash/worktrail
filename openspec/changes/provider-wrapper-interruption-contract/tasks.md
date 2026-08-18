## 1. Wrapper interruption supervision

- [ ] 1.1 Update `src/worktrail/router/skill_dispatch.py` to supervise the provider subprocess, forward wrapper-targeted SIGTERM, tolerate child-exit races, reap the child, and return the same documented interrupted exit outcome as direct child interruption. (Requirements: Interruption produces a recoverable terminal run record; Interruption has a provider-neutral wrapper outcome; Interrupted dispatch leaves no provider child)
      files: src/worktrail/router/skill_dispatch.py

## 2. Hermetic provider lifecycle regression

- [ ] 2.1 Extend `tests/router/fake_internal_dispatch_agent.py` and `tests/router/test_internal_dispatch_lifecycle.py` with the smallest wrapper-versus-child SIGTERM matrix for Claude, Codex, and OpenCode, asserting exact parent `failed_recoverable` state, ownership preservation, exit `130`, and no surviving fake child while keeping fake credentials and all mutable artifacts under `TemporaryDirectory`. (Requirement: Lifecycle regression remains hermetic)
      files: tests/router/fake_internal_dispatch_agent.py, tests/router/test_internal_dispatch_lifecycle.py

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_internal_dispatch_lifecycle.py` and confirm the focused lifecycle suite passes.
- [ ] 3.2 [e2e] Run `worktrail-pre-pr-gate --repo .` and confirm the repository pre-PR gate passes.
