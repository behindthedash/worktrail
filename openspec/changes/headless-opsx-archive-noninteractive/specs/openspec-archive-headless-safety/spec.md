## ADDED Requirements

### Requirement: The bundled archive procedure never blocks on an interactive confirmation
The archive procedure shipped as `skills/openspec-archive-change/SKILL.md` and
`commands/opsx/archive.md` SHALL define, once per file, what makes a run unattended — the
`$AUTO_MODE` marker is `true`, or the `AskUserQuestion` tool is unavailable to the session —
and SHALL document, at every step that would otherwise confirm with the user, the default it
takes in that case. An unattended run SHALL NOT call `AskUserQuestion`, SHALL take the
documented default at each confirming step, and SHALL name every confirmation it skipped, with
the default taken, in its completion summary. A run that is not unattended SHALL prompt at
every confirming step exactly as it does today.

#### Scenario: Incomplete artifacts do not stall an unattended archive
- **WHEN** the procedure runs unattended and `openspec status --change <name> --json` reports
  an artifact that is not `done`
- **THEN** the procedure archives the change, names the incomplete artifact in its summary's
  warnings, and issues no confirmation prompt

#### Scenario: Incomplete tasks do not stall an unattended archive
- **WHEN** the procedure runs unattended and the change's `tasks.md` still contains `- [ ]`
  entries
- **THEN** the procedure reports the incomplete-task count in its summary's warnings and
  proceeds to archive

#### Scenario: An unattended archive takes the recommended sync option
- **WHEN** the procedure runs unattended and the change's delta specs are not yet merged into
  `openspec/specs/<capability>/spec.md`
- **THEN** it takes the "Sync now" option the step marks recommended and archives regardless
  of the sync's outcome, without prompting

#### Scenario: An already-synced change archives without a prompt
- **WHEN** the procedure runs unattended and the change's delta specs are already merged
- **THEN** it takes the "Archive now" option and issues no prompt

#### Scenario: An interactive run is unchanged
- **WHEN** the procedure runs in a session that has the `AskUserQuestion` tool and is not in
  auto mode
- **THEN** every confirming step prompts as before, and no fallback default is taken on the
  user's behalf

### Requirement: An unattended archive refuses a change it was not told to archive
When the run is unattended and no change name was supplied, the procedure SHALL stop with an
error naming the change it requires, SHALL archive nothing, and SHALL NOT select a change on
the caller's behalf. The Guardrails rule that requires prompting for change selection SHALL
carry this carve-out, so it does not read as an unconditional instruction to prompt.

#### Scenario: No change name in an unattended run
- **WHEN** the procedure runs unattended and no change name is given
- **THEN** it stops with an error naming the required change, and no change directory is moved

#### Scenario: The change-selection guardrail carries the carve-out
- **WHEN** the Guardrails section is read
- **THEN** the "always prompt for change selection if not provided" rule states that an
  unattended run treats an absent change name as an error rather than a guess

### Requirement: Every confirming step's fallback is pinned structurally
A test SHALL fail if either shipped archive file stops defining the unattended condition, or
if any numbered step that prompts for confirmation carries no unattended fallback, or if the
change-selection guardrail drops its carve-out — so a later edit cannot reintroduce an
unconditional ask without failing the build.

#### Scenario: A step's fallback is removed
- **WHEN** a prompt-carrying numbered step in either shipped archive file is edited to drop
  its unattended fallback
- **THEN** the test fails, naming the file and the step

#### Scenario: The unattended condition is left undefined
- **WHEN** either shipped archive file stops stating what makes a run unattended
- **THEN** the test fails, naming that file

#### Scenario: The guardrail loses its carve-out
- **WHEN** the change-selection guardrail is edited back to an unconditional prompt
- **THEN** the test fails, naming that file
