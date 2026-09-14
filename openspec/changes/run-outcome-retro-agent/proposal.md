## Why

This is Epic 003 Feature 2 (`docs/specs/epics/003-agent-learning-from-run-outcomes.md`).

Run journals already record why work failed, but nothing learns from them. Across the
`run-*.json` journals under `datalena-worktrees/` (inspected 2026-09-13):
- groups carry `state` and `quarantine_reason`;
- worker entries carry `role`, `task`, `agent`, `scope_escalated`, `blocked_by`, and a `report`
  with `status`, `review_status`, `critical_issues`, `major_issues`, `context_quality`,
  `missing_context`, and `notes`;
- observability markers such as `dependency_file_drift` record safety-net fires.

Claude Code agent `memory:` gives an agent a curated `MEMORY.md` that loads into its system
prompt on every launch. Spikes on 2026-09-12 and 2026-09-13 (Claude Code 2.1.270) confirmed three
things:
- inline `--agents` definitions honor `"memory": "project"`;
- memory loads under worker settings (`--setting-sources project,local`);
- the store lives under the launch cwd.

The docs state that memory never updates itself from tool calls. The agent writes only when its
prompt tells it to, so the curation input has to be supplied deliberately.

## What Changes

- **New package `src/worktrail/learning/`:**
  - `paths.py`:
    - `learning_dir(repo)` returns `worktrail_home()/learning/<repo-name>/`;
    - `retro_memory_path(repo)` returns
      `<learning_dir>/.claude/agent-memory/worktrail-retro/MEMORY.md`;
    - the `RETRO_AGENT_NAME` constant.
  - `digest.py`: a pure `build_outcome_digest(journal)` that returns bounded, structured outcome
    signals and a `has_signal` flag.
  - `retro_agent.md`: the retro agent's curation prompt, shipped as package data.
  - `retro.py`:
    - `run_retro(repo, journal_path, ...)`, which runs the gates: policy opt-in, signal, Claude
      harness, per-repo lock;
    - spawns the memory-enabled agent through `spawnlib.spawn_agent`;
    - checks the memory contract and records a `retro` journal marker;
    - `main()` for the new `worktrail-retro` console script.
- **Policy:** new flat key `agent_learning` in `.worktrail/policy.yaml`, default `false`.
- **Orchestrator:** each run-completion site in `live.py` that calls `_print_usage_report` then
  calls the retro best-effort. The run's return value is unchanged.
- **Packaging:** `worktrail-retro` console script, and `learning/*.md` package data.

## Capabilities

### New Capabilities

- `run-outcome-retro-agent`: an opt-in, lock-guarded, best-effort Claude agent. It turns each
  finished run's outcome digest into curated, evidence-cited notes in a per-repo agent memory
  outside any repository or worktree.

### Modified Capabilities

_None._ The policy key is additive, and no existing requirement changes.

## Impact

- New files:
  - `src/worktrail/learning/__init__.py`
  - `src/worktrail/learning/paths.py`
  - `src/worktrail/learning/digest.py`
  - `src/worktrail/learning/retro.py`
  - `src/worktrail/learning/retro_agent.md`
  - `tests/learning/__init__.py`
  - `tests/learning/test_digest.py`
  - `tests/learning/test_retro.py`
- Changed files:
  - `src/worktrail/router/policy.py`
  - `tests/router/test_policy.py`
  - `src/worktrail/orchestrator/live.py`
  - `tests/orchestrator/test_live_extras.py`
  - `pyproject.toml`
  - `tests/test_packaging_metadata.py`
- **Cost:** at most one Claude spawn per finished run, only in repos with `agent_learning: true`
  and only when the digest has signal.
- **Release scope:** new capability. It waits for v1.1 feature work; `.worktrail/policy.yaml`
  currently sets `release_gate: v1.0`.
- **Plugin surface:** no change. No skill references `worktrail-retro`.
