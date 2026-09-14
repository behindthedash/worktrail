## ADDED Requirements

### Requirement: Retro Is Opt-In Per Repo Policy

The system SHALL add an `agent_learning` top-level boolean to the policy defaults with value
`false`. The system SHALL NOT read a journal, build a digest, take a lock, or spawn an agent for
the retro unless the repo's `.worktrail/policy.yaml` sets `agent_learning: true`.

#### Scenario: Policy without the key

- **WHEN** a run completes in a repo whose `.worktrail/policy.yaml` has no `agent_learning` key
- **THEN** the retro returns `status: skipped` with `reason: disabled`
- **AND** no agent is spawned

#### Scenario: Policy enables learning

- **WHEN** `.worktrail/policy.yaml` sets `agent_learning: true`
- **THEN** `load_policy(repo)["agent_learning"]` is `True`

### Requirement: Outcome Digest Is Deterministic And Bounded

The system SHALL provide a pure `build_outcome_digest(journal)` that returns `spec_id`, `run_id`,
`quarantined_groups`, `worker_signals`, `events`, `unreconciled_tail`, and `has_signal`, derived
only from journal fields.

`quarantined_groups` SHALL list each group record whose `state` is `QUARANTINED`, with its group
name and `quarantine_reason`.

`worker_signals` SHALL hold one item per role entry that has at least one signal:
- `insufficient_context`: `report.context_quality` is `insufficient`
- `critical_issues`: `report.critical_issues` is greater than 0
- `major_issues`: a review-role entry whose `report.major_issues` is greater than 0
- `scope_escalated`: `scope_escalated` is true
- `blocked`: `blocked_by` is non-empty
- `fix_round`: the role is `fix`

Items SHALL be capped at 50 in journal order, with a `truncated` flag. Each item's `notes` and
`missing_context` SHALL be truncated to 500 characters.

`events` SHALL count `event` markers by type, excluding `retro` and `learned_notes`.

The digest SHALL NOT include usage, tool lists, head SHAs, or any field not named here. The same
journal SHALL always produce an identical digest.

#### Scenario: Quarantined group is reported

- **WHEN** the journal has a group `g2` with `state: QUARANTINED` and
  `quarantine_reason: task_failure`
- **THEN** `quarantined_groups` contains `{"group": "g2", "reason": "task_failure"}`
- **AND** `has_signal` is true

#### Scenario: Worker signals are extracted and bounded

- **WHEN** the journal has 60 review entries, each with `report.major_issues: 1` and a
  1,000-character `report.notes`
- **THEN** `worker_signals` has 50 items, each including `major_issues`
- **AND** `truncated` is true
- **AND** every item's `notes` is at most 500 characters

#### Scenario: Excluded fields never appear

- **WHEN** a journal entry carries `usage`, `tools_used`, and `report.head_sha`
- **THEN** none of those values appears anywhere in the digest

### Requirement: Runs Without Outcome Signal Spawn Nothing

When the digest's `has_signal` is false, the system SHALL return `status: skipped` with
`reason: no_signal`, SHALL NOT take the lock, and SHALL NOT spawn an agent.

#### Scenario: Clean run

- **WHEN** learning is enabled and a run's journal has no quarantined group, no worker signal, no
  counted event, and no unreconciled tail evidence
- **THEN** the retro returns `status: skipped`, `reason: no_signal`
- **AND** the spawn function is never called

### Requirement: Retro Runs As A Memory-Enabled Claude Agent Outside Any Repository

When learning is enabled and the digest has signal, the system SHALL resolve the launch cell with
`select_cell` for `REVIEW_DEFAULT_TIER`, preferring `claude`. If that cell's harness is not
`claude`, the system SHALL skip with `reason: claude_harness_unavailable`.

Otherwise it SHALL create `worktrail_home()/learning/<repo-name>/` and call `spawn_agent` with:
- that directory as `cwd`;
- a prompt containing the digest as JSON;
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

#### Scenario: No Claude cell available

- **WHEN** learning is enabled, the digest has signal, and `select_cell` resolves a `codex` cell
- **THEN** the retro returns `status: skipped`, `reason: claude_harness_unavailable`
- **AND** no agent is spawned

#### Scenario: Memory path contract

- **WHEN** `retro_memory_path(repo)` is called for a repo directory named `datalena`
- **THEN** it returns
  `worktrail_home()/learning/datalena/.claude/agent-memory/worktrail-retro/MEMORY.md`

### Requirement: One Retro Writer Per Repo At A Time

Before spawning, the system SHALL take a non-blocking exclusive lock on
`worktrail_home()/learning/<repo-name>/.retro.lock`. When the lock is already held, it SHALL
return `status: skipped` with `reason: locked`, without waiting or spawning.

#### Scenario: Concurrent retro for the same repo

- **WHEN** one retro holds the lock for a repo and a second retro for the same repo starts
- **THEN** the second returns `status: skipped`, `reason: locked`
- **AND** it does not call the spawn function

### Requirement: Retro Never Changes The Run Outcome

The system SHALL map any `Exception`, `SpawnExhausted`, `NoExecutionTarget`, or spawn timeout
during the retro to `status: failed` with a reason. The orchestrator wiring SHALL call the retro
after `_print_usage_report` at each run-completion site, SHALL NOT propagate any exception from
it, and SHALL return the same result it would have returned without the retro.

Every retro outcome, whether completed, skipped, or failed, SHALL be appended to the run journal
as one `{"event": "retro", "status": ..., "reason": ...}` marker through
`progress.append_safety_net_events`.

#### Scenario: Spawn raises

- **WHEN** the spawn function raises `RuntimeError` during a retro
- **THEN** the retro returns `status: failed` with a reason naming the error
- **AND** the run journal gains one `retro` marker with `status: failed`

#### Scenario: Orchestrator result is unchanged by a failing retro

- **WHEN** the retro raises inside the run-completion wiring of `full_real`
- **THEN** `full_real` returns the same result dict it returns with the retro disabled

### Requirement: Retro Memory Contract Is Checked And Recorded

After a completed spawn, the system SHALL run `check_memory_contract` on `retro_memory_path(repo)`.
It reports a violation for each of:
- a missing file;
- a missing `## Notes for workers` section;
- more than 20 bullets in that section;
- a bullet in that section without `(evidence:`;
- a file larger than 25 KB.

The system SHALL record the violations and the file's SHA-256 on the `retro` journal marker as
`contract_violations` and `memory_sha256`. It SHALL NOT modify the memory file.

#### Scenario: Agent wrote a compliant memory file

- **WHEN** a completed retro leaves a `MEMORY.md` whose `## Notes for workers` section has 3
  evidence-cited bullets
- **THEN** the `retro` marker has an empty `contract_violations` list and a `memory_sha256` value

#### Scenario: Agent exceeded the notes cap

- **WHEN** a completed retro leaves 23 bullets under `## Notes for workers`
- **THEN** `contract_violations` names the notes-cap violation
- **AND** the memory file content is unchanged

### Requirement: Operators Can Run The Retro Manually

The package SHALL provide a `worktrail-retro` console script accepting `--repo`, `--journal`,
`--dry-run`, and `--json`. It SHALL run the same retro path the orchestrator runs.

With `--dry-run`, it SHALL print the digest and the gate decision it would make, and SHALL NOT
take the lock or spawn.

The exit code SHALL be 0 for `completed` or `skipped`, 1 for `failed`, and 2 for usage errors.
The packaged `retro_agent.md` SHALL be included in built distributions.

#### Scenario: Dry run on a quarantined journal

- **WHEN** `worktrail-retro --repo <repo> --journal <run.json> --dry-run --json` runs against a
  journal with one quarantined group, in a repo with learning enabled
- **THEN** it prints JSON containing the digest with `has_signal: true` and the would-spawn
  decision
- **AND** it exits 0 without spawning or creating `.retro.lock`

#### Scenario: Console script and package data are declared

- **WHEN** the project metadata is inspected
- **THEN** `worktrail-retro` maps to `worktrail.learning.retro:main`
- **AND** the package data includes `learning/*.md`
