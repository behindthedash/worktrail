## 1. Learning paths and outcome digest (`run-outcome-retro-agent`)

- [ ] 1.1 In `src/worktrail/learning/paths.py` and `src/worktrail/learning/digest.py`, add `learning_dir(repo)`, `retro_memory_path(repo)`, `RETRO_AGENT_NAME`, and the pure `build_outcome_digest(journal)` per design D1-D2. Also create the empty `src/worktrail/learning/__init__.py` and `tests/learning/__init__.py`.
      Write the failing tests first in `tests/learning/test_digest.py`, with journal fixtures shaped like real `run-*.json` files: `entries` with `role`/`task`/`agent`/`report`/`scope_escalated`/`blocked_by`, `groups` records with `state`/`quarantine_reason`, `{"event": ...}` markers, and `unreconciled_tail_evidence`. Cover:
      - a quarantined group is reported;
      - each signal kind is extracted;
      - the 50-item cap with the `truncated` flag;
      - 500-character truncation of `notes` and `missing_context`;
      - `usage`, `tools_used`, and `head_sha` are excluded;
      - `retro` and `learned_notes` markers are excluded from `events`;
      - a clean journal gives `has_signal` false;
      - output is identical across repeated calls;
      - the memory path contract under a temporary `WORKTRAIL_HOME`;
      - a real linked worktree (`git worktree add` under `tmp_path`) and its canonical checkout resolve to the same `learning_dir`, and a non-git directory falls back to its own name.
      files: src/worktrail/learning/__init__.py, src/worktrail/learning/paths.py, src/worktrail/learning/digest.py, tests/learning/__init__.py, tests/learning/test_digest.py
      Covers: Outcome Digest Is Deterministic And Bounded; Retro Runs As A Memory-Enabled Claude Agent Outside Any Repository

## 2. Opt-in policy key (`run-outcome-retro-agent`)

- [ ] 2.1 In `src/worktrail/router/policy.py`, add the flat `agent_learning` boolean to the policy defaults with value `False`, per design D8.
      Write the failing tests first in `tests/router/test_policy.py`: an absent key loads `False`, and `agent_learning: true` in `.worktrail/policy.yaml` loads `True`.
      files: src/worktrail/router/policy.py, tests/router/test_policy.py
      Covers: Retro Is Opt-In Per Repo Policy

## 3. Retro agent, CLI, packaging, and orchestrator wiring (`run-outcome-retro-agent`)

- [ ] 3.1 In `src/worktrail/learning/retro.py` and `src/worktrail/learning/retro_agent.md`, implement:
      - `run_retro(repo, journal_path, *, spawn=spawnlib.spawn_agent, select=select_cell, log=print, timeout=900)`, running the gates in order per design D3-D7 (policy, digest signal, Claude cell, lock), then the inline-agent spawn, `check_memory_contract`, and the single `retro` journal marker through `progress.append_safety_net_events`;
      - the curation prompt, with the section layout, promotion, evidence, and redaction rules from design D7;
      - `main()` for the CLI per design D9.
      In `pyproject.toml`, add the `worktrail-retro = "worktrail.learning.retro:main"` console script and add `learning/*.md` to `[tool.setuptools.package-data]`.
      In `src/worktrail/orchestrator/live.py`, add a private `_run_retro_best_effort(repo, journal_path)` that calls `run_retro`, catches `Exception`, prints one line, and never re-raises. Call it immediately after each `_print_usage_report(journal_path)` run-completion call.
      Write the failing tests first.
      In `tests/learning/test_retro.py`, with a fake `spawn` and `select` and a temporary `WORKTRAIL_HOME` and repo, cover:
      - the `disabled` and `no_signal` skips with no spawn;
      - `claude_harness_unavailable` for a `codex` cell;
      - exact spawn `cwd`, `extra_args` (`--agents` JSON with `memory: project` and `tools` Read/Write/Edit, then `--agent worktrail-retro`), and digest in the prompt;
      - `locked` while another process holds `.retro.lock`;
      - `RuntimeError`, `SpawnExhausted`, and timeout each map to `failed`;
      - contract violations (cap, missing evidence, missing section, oversize) recorded without modifying the file;
      - one `retro` marker per call;
      - `--dry-run --json` prints the digest and decision without a lock or spawn;
      - exit codes 0, 1, and 2.
      In `tests/test_packaging_metadata.py`, assert the console script and package-data declarations.
      In `tests/orchestrator/test_live_extras.py`, cover: a retro that raises leaves `full_real`'s completion result identical to a run with the retro stubbed out, and the retro is invoked once per completion with that run's journal path.
      files: src/worktrail/learning/retro.py, src/worktrail/learning/retro_agent.md, tests/learning/test_retro.py, pyproject.toml, tests/test_packaging_metadata.py, src/worktrail/orchestrator/live.py, tests/orchestrator/test_live_extras.py
      Covers: Retro Is Opt-In Per Repo Policy; Runs Without Outcome Signal Spawn Nothing; Retro Runs As A Memory-Enabled Claude Agent Outside Any Repository; One Retro Writer Per Repo At A Time; Retro Never Changes The Run Outcome; Retro Memory Contract Is Checked And Recorded; Operators Can Run The Retro Manually
      depends: 1.1, 2.1

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, and `openspec validate run-outcome-retro-agent --strict`.
      Then, with a temporary `WORKTRAIL_HOME` and a scratch repo whose `.worktrail/policy.yaml` sets `agent_learning: true`:
      - Run the installed `worktrail-retro` for real against a fixture journal with one quarantined group. Confirm `MEMORY.md` exists at `retro_memory_path` with a `## Notes for workers` or `## Observations` entry citing that spec id, and that the journal gained a `retro` marker with `status: completed`.
      - Run it a second time against a second fixture journal repeating the same failure. Confirm the existing entry's evidence was refreshed or promoted rather than duplicated. This proves the memory reloaded.
      - Confirm from the spawn's stream-json output that the agent made no `Bash` tool call, which verifies the inline `tools` restriction.
      depends: 3.1
