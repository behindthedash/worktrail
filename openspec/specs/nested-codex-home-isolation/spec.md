# nested-codex-home-isolation Specification

## Purpose
Ensures a nested Codex process has a writable child home distinct from the
parent authentication home, so safe session inheritance does not stop an
automatic Worktrail dispatch.
## Requirements
### Requirement: Automatic Codex child home is isolated from the parent home
When Worktrail starts a Codex child without an explicit child-home override,
it SHALL select a writable persistent Worktrail child home that resolves to a
different directory from the inherited parent `CODEX_HOME`, whether or not the
parent home is writable. The parent home SHALL remain the source for verified
ChatGPT authentication inheritance and SHALL NOT be reused as the child
process's `CODEX_HOME`.

#### Scenario: Writable inherited parent home
- **WHEN** a Codex dispatch inherits a writable parent `CODEX_HOME` and no
  explicit child-home override is supplied
- **THEN** the prepared child environment has a distinct writable Worktrail
  `CODEX_HOME`, and authentication inheritance can proceed without treating
  the parent and child as the same directory

#### Scenario: Read-only inherited parent home
- **WHEN** a Codex dispatch inherits a parent `CODEX_HOME` that is not
  writable and no explicit child-home override is supplied
- **THEN** the prepared child environment uses a distinct writable Worktrail
  `CODEX_HOME` rather than the read-only parent home

#### Scenario: Automatic child path would collide with parent
- **WHEN** the normal automatic child-home path resolves to the inherited
  parent `CODEX_HOME`
- **THEN** Worktrail selects a distinct persistent child-home path before
  attempting authentication inheritance

### Requirement: Explicit Codex child-home selection remains caller-controlled
When a caller supplies `--codex-home` or `WORKTRAIL_CODEX_HOME`, Worktrail
SHALL use that explicit path as the Codex child home and SHALL continue to
fail before launch if the selected path is unsafe or cannot be prepared. It
SHALL NOT silently replace an explicit selection with an automatic home.

#### Scenario: Explicit writable child home
- **WHEN** a caller supplies a writable explicit child-home path different
  from the parent `CODEX_HOME`
- **THEN** the nested Codex process is prepared with that explicit path

#### Scenario: Explicit child home equals parent home
- **WHEN** a caller explicitly selects the same directory as the parent
  `CODEX_HOME` while authentication inheritance is enabled
- **THEN** Worktrail refuses the launch before modifying authentication files
  rather than silently selecting another home

### Requirement: Shared dispatch paths preserve automatic home isolation
The skill-dispatch path and the direct orchestrator Codex worker path SHALL
both prepare their child environments through the shared child-home contract,
so an automatic nested launch in either path uses an isolated child home.

#### Scenario: Skill dispatch prepares an automatic child
- **WHEN** skill dispatch launches Codex with no explicit child-home override
- **THEN** its child environment satisfies Automatic Codex child home is
  isolated from the parent home before the Codex command is started

#### Scenario: Direct orchestrator worker prepares an automatic child
- **WHEN** the orchestrator launches a Codex worker in its subscription lane
  with no explicit child-home override
- **THEN** its subprocess environment satisfies Automatic Codex child home is
  isolated from the parent home before the worker command is started
