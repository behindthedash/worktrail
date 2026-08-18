## 1. Persistence and detection module

- [ ] 1.1 Create `src/worktrail/drain/stuck_remediation.py`: `history_path()` (env override `WORKTRAIL_STUCK_REMEDIATION_HISTORY`, default `worktrail_home() / "remediation-history.json"`), `load(path)`/`save(value, path)` following `agent_capacity.py`'s atomic-write pattern, a `DEFAULT_RETENTION` constant, the pure `record_and_detect(history, resumed, now, threshold, retention)` function per design.md D2 (extends the streak for every identity re-affirmed this sweep, drops every other identity, returns the list of identities that reached `threshold`), and the `sweep_and_record(resumed, path, threshold, retention, now)` I/O wrapper reusing `agent_capacity.write_lock` for the load/detect/save sequence. (Requirement: Stuck-remediation detection) (Requirement: Stuck-remediation history retention)

## 2. Wire detection into drain()

- [ ] 2.1 In `src/worktrail/drain/drain.py`: add `stuck_threshold: int = 3` and `stuck_history_path: Optional[Path] = None` fields to `DrainConfig`; add a `--stuck-threshold` CLI flag (default 3) and resolve `stuck_history_path`'s default via `stuck_remediation.history_path()` in `main()`; and wire `stuck_remediation.sweep_and_record` into `drain()` immediately after the pre/post sweep `resumed` merge (design.md D5), under the same `slot == 0 and config.repos_root is not None and not config.dry_run` guard the sweeps themselves already use, logging a `stuck remediation: ...` line per flagged identity and setting `summary["stuck_remediations"]` to the returned list. (Requirement: Stuck-remediation CLI configuration) (Requirement: Backward-compatible summary dict)

## 3. Tests

- [ ] 3.1 Add `tests/drain/test_stuck_remediation.py` covering `record_and_detect`'s streak/threshold/reset semantics (repeated recurrence increments the streak and flags at `threshold`; an identity absent from a sweep's `resumed` drops out of the next history rather than persisting a stale streak; independent tracking across different `(key, repo_name, spec_id)` identities and across different remediation keys for the same repo/spec) and the `load`/`save`/`sweep_and_record` persistence round-trip (a missing or corrupt history file degrades to an empty history; state written by one `sweep_and_record` call is read back by the next).
- [ ] 3.2 In `tests/drain/test_drain.py`, add coverage that `drain()`'s `summary["stuck_remediations"]` is an empty list when no identity recurs, that an identity recurring across `stuck_threshold` sequential `drain()` calls sharing a history path is flagged with the expected streak, and that a `--dry-run` (or `repos_root=None`) invocation never writes the history file.

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm it passes.
- [ ] 4.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm it passes.
