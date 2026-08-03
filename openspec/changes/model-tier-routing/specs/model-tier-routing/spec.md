## ADDED Requirements

### Requirement: Agent-entry schema supports an optional reasoning-effort field
A `routing.tiers`/`routing.roles`/`routing.fallback` agent-entry (validated by
`policy._validate_agent_entry()`) SHALL accept an optional `effort` string field
alongside the existing `agent_cli`/`agent_model` fields. An entry with no `effort` key
SHALL resolve identically to today's behavior (no effort flag passed to the spawned
CLI).

#### Scenario: Agent-entry with effort validates and resolves
- **WHEN** a `routing.tiers` entry is `{agent_cli: codex, agent_model: gpt-5.6-sol, effort: high}`
- **THEN** `resolve_routing()`'s returned entry SHALL include `effort: "high"` alongside `agent_cli`/`agent_model`

#### Scenario: Agent-entry without effort is unaffected
- **WHEN** a `routing.tiers` entry is `{agent_cli: codex, agent_model: gpt-5.6-sol}` (no `effort` key)
- **THEN** `resolve_routing()`'s returned entry SHALL have `effort: None`, and dispatch SHALL behave exactly as before this change

### Requirement: build_cmd() translates a resolved effort into the correct per-agent CLI flag
`spawnlib.build_cmd()` SHALL accept an optional `effort` parameter and, when set,
append the agent-specific reasoning-effort flag to the spawned command: `--effort
<value>` for claude, `-c model_reasoning_effort=<value>` for codex, `--variant <value>`
for opencode. When `effort` is `None` or omitted, `build_cmd()`'s output SHALL be
byte-identical to its pre-change output for the same other arguments.

#### Scenario: Claude command includes --effort
- **WHEN** `build_cmd(prompt, agent="claude", model="opus", effort="xhigh")` is called
- **THEN** the returned argv SHALL include `["--effort", "xhigh"]`

#### Scenario: Codex command includes the -c config override
- **WHEN** `build_cmd(prompt, agent="codex", model="gpt-5.6-sol", effort="high")` is called
- **THEN** the returned argv SHALL include `["-c", "model_reasoning_effort=high"]`

#### Scenario: OpenCode command includes --variant
- **WHEN** `build_cmd(prompt, agent="opencode", model="opencode/deepseek-v4-flash", effort="high")` is called
- **THEN** the returned argv SHALL include `["--variant", "high"]`

#### Scenario: No effort configured leaves the command unchanged
- **WHEN** `build_cmd(prompt, agent="codex", model="gpt-5.6-terra")` is called with no `effort` argument
- **THEN** the returned argv SHALL contain no `-c model_reasoning_effort=...` (or equivalent) flag, identical to this function's behavior before this change

### Requirement: Review role defaults to claude:opus
`routing.roles` SHALL support a `review` entry pinning the review role to a specific
agent+model regardless of the task's own complexity/domain, consistent with the
existing `JUDGMENT_ROLES` precedence rule (role_agent_map wins outright for judgment
roles) already implemented in `dispatch.agent_for()`.

#### Scenario: Review role uses the configured override regardless of task complexity
- **WHEN** `routing.roles: {review: {agent_cli: claude, agent_model: opus}}` is configured and a task with `complexity: trivial` is dispatched for the `review` role
- **THEN** `dispatch.agent_for("review", task, ..., role_agent_map={"review": {"agent_cli": "claude", "agent_model": "opus"}})` SHALL resolve to `claude`/`opus`, never the trivial-complexity tier's agent/model

### Requirement: A 3-tier complexity fallback resolves independent of task-purpose classification
`routing.tiers` SHALL support plain `trivial`/`standard`/`hard` complexity keys
(matching today's existing task-frontmatter `complexity` field), each resolving to an
explicit agent/model pair, usable immediately without any task-purpose classification
mechanism.

#### Scenario: A hard-complexity task resolves to the hard tier's agent/model
- **WHEN** `routing.tiers: {hard: {agent_cli: codex, agent_model: gpt-5.6-sol}}` is configured and a task has `complexity: hard`
- **THEN** `dispatch.agent_for()` SHALL resolve that task to `codex`/`gpt-5.6-sol` via the existing `tier_map` resolution path

### Requirement: Configuring nothing new preserves current behavior exactly
A repo or task that configures no `effort` field and no new tier/role entries SHALL
dispatch identically to how it did before this change — no schema addition in this
change SHALL alter behavior unless explicitly configured.

#### Scenario: Untouched repo/task is unaffected
- **WHEN** a repo has no `routing.tiers`/`routing.roles` entries using `effort`, and a task carries no tier-matching frontmatter beyond what already existed
- **THEN** dispatch resolves via the same precedence and produces the same `build_cmd()` argv as before this change shipped
