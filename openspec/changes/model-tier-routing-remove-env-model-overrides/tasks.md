## 1. spawnlib resolution change

- [ ] 1.1 Implement the "Default model resolution is config-file driven only" requirement: in `src/worktrail/orchestrator/spawnlib.py`, rewrite `default_model_for_agent()` (line ~415) to drop both `os.environ.get("ORCH_*_MODEL")` lookups so codex/opencode resolve `defaults.get(agent) or DEFAULT_*_MODEL` uniformly with claude
- [ ] 1.2 Update the rationale comment block above the `DEFAULT_*_MODEL` constants (lines ~364-375) to describe the two-layer precedence (`model-defaults.yaml` > hardcoded constant) without the env-var layer

## 2. Test updates

- [ ] 2.1 In `tests/orchestrator/test_resilience_helpers.py`, delete `test_opencode_model_override_remains_supported` and `test_explicit_env_var_wins_over_file` (they assert removed behavior)
- [ ] 2.2 Add replacement tests: with `ORCH_OPENCODE_MODEL`/`ORCH_CODEX_MODEL` patched into the environment, `default_model_for_agent()` returns the hardcoded constant; and with a model-defaults file entry present, the file value wins despite the ambient vars
- [ ] 2.3 Simplify `ModelDefaultsFileTest.setUp`'s explicit pop-and-restore of `ORCH_CODEX_MODEL`/`ORCH_OPENCODE_MODEL` (no longer load-bearing)
- [ ] 2.4 Update the hermeticity comments referencing the removed vars in `tests/orchestrator/test_spawnlib.py` (~line 1404) and any other test fixtures that instruct isolation from them

## 3. Verification

- [ ] 3.1 [e2e] Repo-wide grep confirms no remaining readers of `ORCH_OPENCODE_MODEL`/`ORCH_CODEX_MODEL` outside historical fixture text (`tests/fixtures/classifier_corpus.json`) and this change's own artifacts
- [ ] 3.2 [e2e] `PYTHONPATH=src pytest -q` green
- [ ] 3.3 [e2e] `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` golden-record regression green
