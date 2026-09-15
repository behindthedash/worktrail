## Context

See `proposal.md` for why the notes must reach workers through the prompt.

Current state:
- `dispatch.build_worker_prompt` assembles one list of lines. The order is: role header, agent,
  worktree/branch, `Read first, in order:`, `Scope`, `Task`, then `Hard rules:`.
- `WorkerPromptCtx` is a dataclass whose optional fields default to `None`.
- `LiveSpawn.__call__` in `live.py` is the only production call site. It builds the ctx dict
  per spawn.
- `LiveSpawn.pre_commit_cmd` is a lazy property that reads `_load_policy(self.repo)` once on
  first access. That is the precedent for once-per-run policy-derived context.
- The run journal already carries observability-only `{"event": ...}` markers, for example
  `dependency_file_drift`, alongside worker entries.
- Feature 2 (`run-outcome-retro-agent`) owns the memory location, `retro_memory_path(repo)`, and
  the `## Notes for workers` section contract.

## Goals / Non-Goals

**Goals:**
- Every worker in an opted-in repo sees the curated notes, on any harness.
- Prompt size stays bounded no matter what the retro wrote.
- A run's prompts are reproducible: one snapshot per run, identified by SHA-256 in the journal.
- A repo without notes gets byte-identical prompts.

**Non-Goals:**
- Filtering notes per task, file, or role. The retro curates the whole list.
- Rendering notes in group, stack-conflict, compile, triage, or drain prompts.
- Writing or editing the memory file. That is Feature 2's job.

## Decisions

### D1: Inject in `build_worker_prompt`, not through Claude agent memory

Only the retro agent's own system prompt loads Claude agent memory, and Codex and OpenCode have
no equivalent. The dispatch layer is harness-agnostic, so plain prompt text is the one channel
that reaches every worker.

### D2: Place the block immediately before `Hard rules:`

Workers read the task and scope first, then the advisory notes, then the hard rules. Putting the
hard rules last keeps them the final instruction. The heading says the task, scope, and hard rules
win on any conflict.

### D3: Bounded, whole-bullet loading

At most 20 bullets and 4,000 characters, truncated only between bullets. A partial bullet can
invert a lesson's meaning ("never X" cut down to "never"), so it is never emitted.

A malformed or oversized file degrades to fewer notes or none. It never produces an error.

### D4: Gate on `agent_learning: true` and snapshot once per `LiveSpawn`

One policy key controls both the retro and the injection, so rollback is a single flag. A lazy
property that mirrors `pre_commit_cmd` reads the policy and the file on first access.

A retro from another concurrent run in the same repo cannot change a run's prompts mid-flight.

### D5: One journal marker per run

The first prompt rendered with notes appends `{"event": "learned_notes", "sha256": ..., "bullets": N}`
through the same observability-only marker path `dependency_file_drift` uses. Outcome rates can
then be grouped by notes revision to judge whether the notes help.

## Risks / Trade-offs

- **Bad notes mislead workers:** they are advisory and labeled as such. Size is capped, hard rules
  come last, and the SHA in the journal lets a regression be traced to a notes revision.
- **Prompt tokens:** at most about 4,000 characters per worker prompt, only in opted-in repos.
- **Golden replay drift:** absent notes render nothing, and `orchestrate check` runs without a
  learning directory, so recorded prompts are unchanged.
