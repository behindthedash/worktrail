## 1. Codex api auth lane in the spawn path

- [x] 1.1 In `src/worktrail/orchestrator/spawnlib.py`, make `spawn_agent`'s `_prepare_child_env` resolve the codex branch from the cell's pool: `pool: api` requires `cell.auth["codex_home"]` (else `OperatorConfigError` naming the target and the `auth: {codex_home: <path>}` remedy) and an existing `auth.json` under that expanded path (existence check only; else `OperatorConfigError` naming the path and `codex login --with-api-key`), then calls `prepare_codex_child_environment(<that path>, inherit_auth=False)`; every other codex pool keeps today's `prepare_codex_child_environment()` defaults. Tests in `tests/orchestrator/test_spawnlib.py`. (Requirement: Harness auth follows the target's pool)
- [x] 1.2 In `tests/orchestrator/test_spawnlib.py`, cover the four codex scenarios: api cell with a provisioned home sets `CODEX_HOME` to it with `inherit_auth=False` (patch `prepare_codex_child_environment` and assert its arguments); api cell without `auth.codex_home` raises before any subprocess launch; api cell with a home lacking `auth.json` raises naming the provisioning step; subscription cell calls `prepare_codex_child_environment()` with defaults exactly as before. (Requirement: Harness auth follows the target's pool); depends on 1.1

## 2. Verification

- [ ] 2.1 [e2e] `PYTHONPATH=src pytest -q tests/orchestrator/test_spawnlib.py` green, and the full pre-PR gate (`worktrail-preflight run`) passes including the golden `orchestrate check` replay. (Requirement: Harness auth follows the target's pool)
