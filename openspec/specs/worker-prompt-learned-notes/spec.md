# worker-prompt-learned-notes Specification

## Purpose
Loads learned notes from the retro memory contract and renders them in worker prompts for every role
and harness, snapshotted once per run for opted-in repos and recorded in the run journal — leaving
prompts byte-identical when no notes exist. Prevents each run from rediscovering lessons a previous
run already paid for.
## Requirements
### Requirement: Learned Notes Are Loaded From The Retro Memory Contract

The system SHALL provide `load_learned_notes(repo)`, which reads the file at
`retro_memory_path(repo)` and returns the bullet lines under its `## Notes for workers` heading.
The section ends at the next level-2 heading or end of file.

The result SHALL keep at most the first 20 bullets and at most 4,000 characters, truncating only
at a bullet boundary.

It SHALL return `None` when the file is absent or unreadable, the section is missing, or the
section contains no bullets. It SHALL NOT raise.

#### Scenario: Memory file is absent

- **WHEN** `load_learned_notes(repo)` runs and no file exists at `retro_memory_path(repo)`
- **THEN** it returns `None`

#### Scenario: Section is missing

- **WHEN** the memory file exists but has no `## Notes for workers` heading
- **THEN** `load_learned_notes(repo)` returns `None`

#### Scenario: Bullet count is capped

- **WHEN** the `## Notes for workers` section contains 25 bullets
- **THEN** `load_learned_notes(repo)` returns exactly the first 20 bullets

#### Scenario: Character size is capped at a bullet boundary

- **WHEN** the section's first 20 bullets total more than 4,000 characters
- **THEN** the returned text is at most 4,000 characters
- **AND** it consists only of whole bullets

#### Scenario: Undecodable file

- **WHEN** the memory file contains bytes that are not valid UTF-8
- **THEN** `load_learned_notes(repo)` returns `None` without raising

### Requirement: Worker Prompts Render Learned Notes For Every Role And Harness

When `WorkerPromptCtx.learned_notes` is a non-empty string, `build_worker_prompt` SHALL render a
block immediately before the `Hard rules:` line. The block is headed:
`Learned notes from past runs in this repo (advisory; the task, scope, and hard rules win on any conflict):`
The notes text follows the heading.

This SHALL apply to every role in `ROLES`, whatever the resolved agent or harness.

#### Scenario: Implement prompt includes the notes before hard rules

- **WHEN** `build_worker_prompt` renders the implement role with `learned_notes` set to a
  two-bullet string
- **THEN** the prompt contains the learned-notes heading followed by both bullets
- **AND** the heading appears after the `Task:` line and before the `Hard rules:` line

#### Scenario: Every role includes the notes

- **WHEN** `build_worker_prompt` renders each role in `ROLES` with `learned_notes` set
- **THEN** every rendered prompt contains the learned-notes heading

#### Scenario: Non-Claude default agent still receives the notes

- **WHEN** `build_worker_prompt` renders with `default_agent` set to `codex` and `learned_notes`
  set
- **THEN** the prompt contains the learned-notes heading and bullets

### Requirement: Absent Notes Leave Worker Prompts Byte-Identical

When `WorkerPromptCtx.learned_notes` is `None` or an empty string, `build_worker_prompt` SHALL
return output byte-identical to the output for the same context without a `learned_notes` key.

#### Scenario: None renders the pre-existing prompt

- **WHEN** `build_worker_prompt` renders a role once with `learned_notes=None` and once from a
  context dict without the key
- **THEN** the two prompts are byte-identical

#### Scenario: Golden replay stays green

- **WHEN** `python3 -m worktrail.orchestrator.orchestrate check` runs with no learning directory
  under `WORKTRAIL_HOME`
- **THEN** it passes

### Requirement: Notes Are Injected Only For Opted-In Repos And Snapshotted Once Per Run

`LiveSpawn` SHALL resolve learned notes at most once per instance, and only when the repo's
`.worktrail/policy.yaml` sets `agent_learning: true`. It SHALL pass that same resolved value to
every worker prompt it builds, even if the memory file changes after the first resolution.

#### Scenario: Policy disabled with a memory file present

- **WHEN** the repo policy does not set `agent_learning: true`
- **AND** a memory file with worker notes exists for that repo
- **THEN** no worker prompt built by `LiveSpawn` contains the learned-notes heading
- **AND** the memory file is not read

#### Scenario: Memory file changes mid-run

- **WHEN** `agent_learning: true` is set and `LiveSpawn` has built a worker prompt with notes
- **AND** the memory file is then rewritten with different notes
- **THEN** later prompts from the same `LiveSpawn` carry the originally resolved notes

### Requirement: Injected Notes Are Recorded In The Run Journal

The first time a `LiveSpawn` builds a worker prompt with a non-empty learned-notes value, the
system SHALL append exactly one run-journal entry. The entry SHALL carry `event: "learned_notes"`,
a `sha256` field with the hex SHA-256 of the notes text, and a `bullets` field with the bullet
count.

#### Scenario: Several spawns with notes record one entry

- **WHEN** a `LiveSpawn` with notes builds three worker prompts
- **THEN** the run journal contains exactly one `learned_notes` entry, with the notes' SHA-256 and
  bullet count

#### Scenario: No notes records no entry

- **WHEN** a `LiveSpawn` resolves learned notes to `None` and builds worker prompts
- **THEN** the run journal contains no `learned_notes` entry

