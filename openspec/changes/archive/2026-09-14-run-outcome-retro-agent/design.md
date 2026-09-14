## Context

See `proposal.md` for the journal fields and the spike facts.

Relevant current code:
- `spawnlib.spawn_agent(prompt, cwd, *, tier, prefer, extra_args, timeout, log, ...)` resolves a
  cell with `runtime.selection.select_cell(routing, tier, prefer=...)`. For Claude cells it
  launches `claude -p <prompt> --permission-mode bypassPermissions --output-format stream-json`.
  - `_with_default_setting_sources` prepends `--setting-sources project,local` and the
    worktree-guard `--settings`.
  - `extra_args` are appended as given, for every harness.
- `spawnlib.SpawnExhausted` and `NoExecutionTarget` signal that no cell could run.
- `dispatch.REVIEW_DEFAULT_TIER = "t1-deep"` is the default judgment tier.
- `progress.append_safety_net_events(journal_path, events)` appends observability-only
  `{"event": ...}` markers, which `reconcile_from_journal` skips on resume.
- `router.policy.load_policy(repo)` merges `.worktrail/policy.yaml` over defaults. `live.py`
  reads it through `_load_policy`.
- `shared.homedir.worktrail_home()` honors `$WORKTRAIL_HOME`, which keeps tests hermetic.
- `full_real` finishes with `progress.set_phase(journal_path, "done")`, then
  `_print_usage_report(journal_path)`, then `=== PIPELINE RUN COMPLETE ===`. A second completion
  site also calls `_print_usage_report`.

## Goals / Non-Goals

**Goals:**
- Learn from outcomes (quarantines, review findings, insufficient context, scope escalation, fix
  rounds, safety-net events), not from tool-call streams.
- Keep curated memory per repo, outside every repository and worktree, with one writer at a time.
- Keep cost opt-in and zero for clean runs.
- Make it impossible for the retro to change a run's outcome.

**Non-Goals:**
- Injecting notes into worker prompts (Feature 3, `worker-prompt-learned-notes`).
- Reading worker transcripts or stream-json tool events.
- Cross-repo aggregation.
- Harnesses other than Claude for the retro itself. Claude agent memory is Claude-only.
- Asynchronous or detached retro execution.

## Decisions

### D1: Deterministic digest first, model second

`build_outcome_digest(journal)` is pure Python. It produces:

- `spec_id` and `run_id`.
- `quarantined_groups`: `[{group, reason}]` for each group record with `state == "QUARANTINED"`.
- `worker_signals`: one item per role entry with at least one signal, in journal order, capped
  at 50 items with a `truncated` flag. Each item carries `task`, `role`, `agent`, and `signals`.
  The possible signals are:

  | Signal | Condition |
  |---|---|
  | `insufficient_context` | `report.context_quality == "insufficient"` |
  | `critical_issues` | `report.critical_issues > 0` |
  | `major_issues` | a review role with `report.major_issues > 0` |
  | `scope_escalated` | `scope_escalated` is true |
  | `blocked` | `blocked_by` is non-empty |
  | `fix_round` | the role is `fix` |

  An item also carries `notes` and `missing_context`, each truncated to 500 characters.
- `events`: counts by `event` type, excluding the retro's own `retro` marker and Feature 3's
  `learned_notes` marker.
- `unreconciled_tail`: true when the journal's `unreconciled_tail_evidence` is non-empty.
- `has_signal`: true when any of the above is non-empty or true.

A clean run makes `has_signal` false and spawns nothing, so cost scales with trouble, not with
run count. The prompt carries the digest only, never transcripts, tool output, or environment.

### D2: Memory lives in `worktrail_home()/learning/<repo-name>/`, and the agent runs there

The spawn cwd is `learning_dir(repo)`, and the agent declares `memory: project`. Its memory is
therefore `learning_dir/.claude/agent-memory/worktrail-retro/MEMORY.md`, a path verified by
spike.

*Alternatives rejected:*
- **`memory: user`** (`~/.claude/agent-memory/`): one file shared by every repo under Claude's
  25 KB auto-load cap, outside `$WORKTRAIL_HOME` and so not hermetic in tests.
- **The target repo:** dirties the canonical checkout, or leaks into PRs. Feature 1 exists to
  stop exactly that.

The key is the name of the **canonical** checkout, resolved through
`gitnexus_preflight.canonical_repo_root(repo)`, not the basename of whatever path was passed in.
Keying by the given path's basename would split one repo's memory across every worktree name the
retro is ever invoked with. Run records show that shape today: a PR landed from worktree
`agent-learning-epic` recorded its run under `worktrail_home()/runs/agent-learning-epic/`. When
the resolution returns `None` (not a git checkout), the resolved path's own name is used.

### D3: Inline `--agents` definition built from package data, not a plugin agent

`retro.py` builds this definition:
```json
{"worktrail-retro": {"description": "...", "prompt": "<retro_agent.md>", "tools": ["Read", "Write", "Edit"], "memory": "project"}}
```
It passes `extra_args=["--agents", <json>, "--agent", "worktrail-retro"]`.

Reasons:
- The package is runtime-agnostic, and AGENTS.md forbids plugin-path resolution.
- Worker spawns exclude user-level settings, so a user-installed plugin agent is not guaranteed
  to load.
- Spike: inline definitions with `"memory": "project"` loaded their memory in both a git and a
  non-git cwd.

`tools` limits the agent to memory editing, and matters because spawns run under
`bypassPermissions`. **Assumption**, verified by task 2.1: inline `tools` actually restricts the
toolset.

### D4: Claude-only cell, resolved before launch

`extra_args` are appended for every harness, and `--agents` is Claude-only. Before spawning,
`run_retro` calls `select_cell(routing, REVIEW_DEFAULT_TIER, prefer="claude")`. If the selected
cell's harness is not `claude`, the retro is skipped with reason `claude_harness_unavailable`.

If capacity fallback moves a later launch to another harness, the launch fails. D6 contains that
failure.

### D5: One writer per repo, through a non-blocking lock

`fcntl.flock(LOCK_EX | LOCK_NB)` on `learning_dir/.retro.lock`. If the lock is held, the retro is
skipped with reason `locked`. It never waits: the next run's digest gets another chance, and
blocking a finished orchestrator on another run's retro is worse than skipping one curation.

### D6: Best-effort containment and a journal marker

`run_retro` returns `{"status": "completed" | "skipped" | "failed", "reason": str, ...}`.
`Exception`, `SpawnExhausted`, `NoExecutionTarget`, and the spawn `timeout` (900 s) map to
`failed`.

The live wiring wraps the call in `try/except Exception`. It never re-raises, never changes
`full_real`'s return value, and prints one line.

Every outcome appends `{"event": "retro", "status", "reason", "memory_sha256"?, "contract_violations"?}`
through `progress.append_safety_net_events`.

### D7: Memory contract, checked deterministically

The retro prompt (`retro_agent.md`) requires this `MEMORY.md` layout:

- `# worktrail-retro memory: <repo-name>`
- `## Notes for workers`: at most 20 bullets. Each is an actionable instruction a future worker
  can follow, ending with `(evidence: <spec_id> <group-or-task> <YYYY-MM-DD>[; ...])`. A pattern
  is promoted here only when at least two runs evidence it, or one quarantine has an unambiguous
  cause.
- `## Observations`: at most 30 single-run patterns awaiting confirmation, each with evidence.

The prompt also tells the agent to:
- merge duplicates, refresh evidence on a repeat, and rewrite or remove anything the new digest
  contradicts;
- never record credentials, tokens, environment values, home-directory paths, or verbatim worker
  output;
- edit only `MEMORY.md`;
- keep the file under 200 lines.

After the spawn, `check_memory_contract(path)` returns violations:
- missing file;
- missing `## Notes for workers` section;
- more than 20 notes;
- a note bullet without `(evidence:`;
- file size over 25 KB.

Violations are recorded, not repaired. Feature 3's loader caps what reaches workers regardless.

### D8: Opt-in flag

`agent_learning` is a flat boolean top-level policy key, default `false`. It is the only switch;
Feature 3 reuses it for injection.

### D9: Manual CLI

`worktrail-retro --repo <path> --journal <run-*.json> [--dry-run] [--json]` runs the same
`run_retro`. `--dry-run` prints the digest and the gate decision it would make, without locking or
spawning.

Exit codes: 0 for `completed` or `skipped`, 1 for `failed`, 2 for usage errors.

## Risks / Trade-offs

- **Retro latency at run end:** bounded by the 900 s timeout, and incurred only on signal runs in
  opted-in repos. Detaching the retro is a non-goal until real latency data justifies it.
- **Memory reload flake:** one of two non-git inline-agent spike runs printed an empty memory
  line. Task 2.1 runs the retro twice and checks the second run refreshed the first run's evidence
  rather than duplicating it.
- **Curation quality:** the evidence-citation and promotion rules plus the contract check limit
  drift. Operators review `MEMORY.md` by hand before Feature 3 is enabled (epic release strategy).
- **Claude Code memory contract changes:** the paths are isolated in `paths.py`, and a contract
  violation surfaces in the journal instead of failing silently.
