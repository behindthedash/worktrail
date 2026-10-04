## MODIFIED Requirements

### Requirement: Retro Runs As A Memory-Enabled Claude Agent Outside Any Repository

When learning is enabled and the digest has signal, the system SHALL resolve the launch cell with
`select_cell` for `REVIEW_DEFAULT_TIER`, preferring the first declared `routing.targets` entry
whose harness is `claude` (resolved with `prefer_target_for_harness("claude")`) -- and with no
preference when the routing declares no claude target, so such a machine degrades to the row's
own order rather than failing. The bare harness name SHALL NOT be passed as `prefer` itself. If
that cell's harness is not `claude`, the system SHALL skip with
`reason: claude_harness_unavailable`.

Otherwise it SHALL create `worktrail_home()/learning/<repo-name>/` and call `spawn_agent` with:
- that directory as `cwd`;
- a prompt containing the digest as JSON;
- the same resolved preference (`prefer_target_for_harness("claude")`, resolved fresh);
- `extra_args` defining an inline `worktrail-retro` agent through `--agents`, with
  `"memory": "project"`, `"tools": ["Read", "Write", "Edit"]`, and the packaged `retro_agent.md`
  as its prompt;
- `--agent worktrail-retro`.

The system SHALL NOT use the target repository, or any of its worktrees, as the retro cwd.

`<repo-name>` SHALL be the directory name of the canonical checkout that owns `repo`'s git common
directory, resolved with `gitnexus_preflight.canonical_repo_root`. It SHALL fall back to `repo`'s
own resolved directory name only when that resolution returns `None`. A linked worktree and its
canonical checkout therefore share one learning directory.

#### Scenario: Worktree path shares the canonical checkout's learning directory

- **WHEN** `learning_dir(repo)` is called once with a canonical checkout named `worktrail` and
  once with a linked worktree of it named `agent-learning-epic`
- **THEN** both calls return `worktrail_home()/learning/worktrail/`

#### Scenario: Spawn arguments for a signal run

- **WHEN** learning is enabled, the digest has signal, and the resolved cell harness is `claude`
- **THEN** the spawn function receives a `cwd` equal to
  `worktrail_home()/learning/<repo-name>/`
- **AND** its `extra_args` contain `--agents` with a `worktrail-retro` definition whose `memory`
  is `project` and whose `tools` are exactly `Read`, `Write`, `Edit`, followed by
  `--agent worktrail-retro`
- **AND** its prompt contains the digest JSON

#### Scenario: A declared claude target is actually preferred

- **WHEN** the routing declares `claude-sub` and `codex-sub`, both with cells in
  `REVIEW_DEFAULT_TIER`, and `codex-sub` comes first in file order
- **THEN** the gate's `select_cell` call SHALL receive `prefer="claude-sub"`, and a ready retro
  SHALL call `spawn_agent` with that same resolved preference instead of the bare harness name

#### Scenario: No Claude target declared still degrades softly

- **WHEN** the routing declares no target whose harness is `claude`
- **THEN** the gate's `select_cell` call SHALL receive no preference (not the name `claude`),
  and -- if the served cell's harness is not `claude` -- the retro SHALL skip with
  `reason: claude_harness_unavailable` rather than failing with `UndeclaredPrefer`

#### Scenario: No Claude cell available

- **WHEN** learning is enabled, the digest has signal, and `select_cell` resolves a `codex` cell
- **THEN** the retro returns `status: skipped`, `reason: claude_harness_unavailable`
- **AND** no agent is spawned

#### Scenario: Memory path contract

- **WHEN** `retro_memory_path(repo)` is called for a repo directory named `datalena`
- **THEN** it returns
  `worktrail_home()/learning/datalena/.claude/agent-memory/worktrail-retro/MEMORY.md`
