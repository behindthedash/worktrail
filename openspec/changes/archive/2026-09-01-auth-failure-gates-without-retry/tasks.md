## 1. Auth-class gating (`model-tier-routing`)

- [x] 1.1 `agent_capacity.classify_failure`: add "refresh token" / "log out and sign in" to the auth tokens; `DEFAULT_COOLDOWNS["auth"] = 86400`.
- [x] 1.2 `spawnlib.spawn_agent`: on an auth-class infra failure, treat the retry budget as exhausted (gate + hop) with an operator log line.
- [x] 1.3 Regression tests: `tests/orchestrator/test_agent_capacity.py` (wording, cooldown) and `tests/orchestrator/test_spawnlib.py::InfraFailureFallback::test_auth_failure_gates_the_cell_without_burning_retries`.
