## MODIFIED Requirements

### Requirement: Session-Touched Durable-Artifact Detection

The Stop hook SHALL use its existing single transcript pass to detect every file path under
`docs/specs/**` or `openspec/changes/**` that the session wrote, whether through edit/write tools
or Bash commands. It SHALL treat each such path as a durable artifact tracking this session's
work.

A Bash command SHALL mark only the path(s) it writes:
- an output-redirect target
- a `tee` file operand
- a `cp` or `mv` destination
- an `mv` source, because the move removes it
- a file operand of `sed -i`, `touch`, `mkdir`, or `rm`

A durable-artifact path that appears elsewhere in the same command SHALL NOT be marked. That
includes an operand or quoted argument of a command that does not write it, the script argument
of `sed`, and an operand of another command in the same pipeline or list.

A redirect that discards output to `/dev/null` (for example `2>/dev/null`, `>/dev/null`, or
`&>/dev/null`) SHALL NOT count as a write. Neither SHALL a file-descriptor duplication (for
example `2>&1` or `>&2`).

#### Scenario: Edited OpenSpec change triggers detection

- **WHEN** the session transcript shows an Edit tool call whose `file_path` is under
  `openspec/changes/<name>/`
- **THEN** the hook reports `<name>` as a session-touched durable artifact

#### Scenario: Read-only spec access does not trigger detection

- **WHEN** the session only read spec files (no edit/write tool calls or Bash writes touching
  `docs/specs/**` or `openspec/changes/**`)
- **THEN** no session-touched durable artifact is reported

#### Scenario: Discarded or duplicated fd redirect is not a write

- **WHEN** the only session commands that name `docs/specs/**` or `openspec/changes/**` paths
  are read-only commands
- **AND** their only redirects discard output to `/dev/null` or duplicate a file descriptor, such
  as `git ls-tree --name-only origin/dev docs/specs/005-x/changes/ docs/specs/001-y/changes/ 2>/dev/null`
  or `ls openspec/changes/foo 2>&1 | head`
- **THEN** no session-touched durable artifact is reported

#### Scenario: Durable path mentioned beside an unrelated write is not marked

- **WHEN** a Bash command writes only outside the durable trees and mentions a durable-artifact
  path in some other position, such as
  `npm ci >/dev/null && worktrail-run-record note --text 'see docs/specs/005/changes'` or
  `pytest -q > /tmp/out.txt && grep -r todo docs/specs/001-task/`
- **THEN** no session-touched durable artifact is reported

#### Scenario: Bash write marks only the path it writes

- **WHEN** the session runs `cp docs/specs/001-task/design.md openspec/changes/new-idea/design.md`
  and `sed -i 's#docs/specs/old#docs/specs/new#' openspec/changes/new-idea/tasks.md`
- **THEN** `openspec/changes/new-idea/design.md` and `openspec/changes/new-idea/tasks.md` are
  reported as session-touched durable artifacts
- **AND** neither `docs/specs/001-task/design.md` nor any path inside the `sed` script is
  reported

### Requirement: Downgrade-To-Suggestion On Dedup Hit

When the mechanical dedup check reports any hit, the Stop hook's instruction SHALL block
auto-capture of the follow-up that the matched artifacts already track. The instruction SHALL:
- name the matched durable artifacts
- forbid capturing a brief for work they track
- require a suggestion-only line naming the resume command instead

The block SHALL state that it suppresses capture only for that tracked follow-up. It SHALL also
state that it never suppresses capture of a distinct defect the matched artifacts do not track;
such a defect remains subject to the base instruction's mandatory defect capture.

The instruction SHALL permit capturing tracked work only when the agent has an explicit
justification. That justification MUST be written into the captured brief itself, as a
dedup-justification section that names the tracked artifact and explains why a separate brief is
warranted.

#### Scenario: Hit downgrades to suggestion-only

- **WHEN** the session touched `openspec/changes/003-tailwind-v4-migration/` and the hook fires
- **THEN** the printed instruction names that change, forbids auto-capturing a brief for work it
  tracks, and requires a suggestion-only line with the resume command

#### Scenario: Dedup hit never suppresses capture of an untracked defect

- **WHEN** the dedup gate block is emitted
- **THEN** the block limits its suppression to the follow-up the named artifacts already track
- **AND** it states that a distinct verified defect those artifacts do not track must still be
  captured

#### Scenario: Explicit justification escape hatch is stated in the instruction

- **WHEN** the dedup gate block is emitted
- **THEN** the block states the only escape hatch: capture requires explicit justification
  recorded as a dedup-justification section inside the brief text

#### Scenario: No hit leaves the instruction unchanged

- **WHEN** the dedup check reports no hits
- **THEN** the hook's instruction is byte-for-byte identical to the base instruction plus any
  deferred-work block, with no dedup gate block
- **AND** the base instruction here means the Stop hook's current instruction, including its
  mandatory defect-capture step and its EXCEPTIONAL-VALUE gate for forward-looking ideas
