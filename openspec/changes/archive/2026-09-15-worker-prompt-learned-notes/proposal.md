## Why

This is Epic 003 Feature 3 (`docs/specs/epics/003-agent-learning-from-run-outcomes.md`).

Epic 003 Feature 2 (`run-outcome-retro-agent`) curates a per-repo `MEMORY.md` under
`worktrail_home()/learning/<repo-name>/`, with a `## Notes for workers` section built from run
outcomes. Workers never see it:
- Claude Code auto-loads an agent's `MEMORY.md` only into that agent's own system prompt.
- Codex and OpenCode workers cannot use Claude agent memory at all.

Every orchestrator worker prompt is rendered in one place, `dispatch.build_worker_prompt`, called
from `LiveSpawn.__call__` in `live.py`. Rendering the notes there reaches every role on every
harness.

## What Changes

- **`load_learned_notes(repo)`** (new, in `src/worktrail/learning/notes.py`):
  - reads Feature 2's `retro_memory_path(repo)`;
  - returns the bullets under `## Notes for workers`, capped at 20 bullets and 4,000 characters,
    truncated only between bullets;
  - returns `None` when there is nothing usable, and never raises.
- **Prompt block:**
  - `WorkerPromptCtx` gains `learned_notes: str | None = None`.
  - When it is set, `build_worker_prompt` renders an advisory learned-notes block immediately
    before `Hard rules:`.
  - When it is not set, the prompt is byte-identical to today.
- **Once-per-run snapshot:** `LiveSpawn` gains a lazy `learned_notes` property that mirrors
  `pre_commit_cmd`. It resolves once per run, only when the repo policy sets
  `agent_learning: true`, and passes the same value to every worker.
- **Journal record:** the first spawn that renders notes appends one `learned_notes` journal
  entry with the SHA-256 of the notes text and the bullet count.

## Capabilities

### New Capabilities

- `worker-prompt-learned-notes`: curated per-repo learned notes are rendered as advisory context
  in every worker prompt for opted-in repos. The size is bounded, the notes are snapshotted once
  per run, and their use is recorded in the journal.

### Modified Capabilities

_None._

## Impact

- `src/worktrail/learning/notes.py` and `tests/learning/test_notes.py` (new).
- `src/worktrail/orchestrator/dispatch.py` and `tests/orchestrator/test_dispatch.py`.
- `src/worktrail/orchestrator/live.py` and `tests/orchestrator/test_live_extras.py`.
- **Depends on** `run-outcome-retro-agent`:
  - `worktrail.learning.paths.retro_memory_path`
  - the `## Notes for workers` section contract
  - the `agent_learning` policy default
- Repos without `agent_learning: true` get no behavior change. The golden record/replay
  (`orchestrate check`) must stay green.
