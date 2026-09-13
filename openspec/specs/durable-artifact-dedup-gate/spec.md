## Purpose

A mechanical dedup gate on Worktrail's follow-up capture path: before the Stop hook instructs an
agent to auto-capture a handoff, and before handoff capture writes a brief, the system checks
whether a durable artifact (session-touched spec/OpenSpec change, run record finishing
`planned_ready_for_implementation`, merged docs-only spec PR, spec-slug or open-PR overlap)
already tracks the same follow-up, and downgrades or warns instead of duplicating work onto two
lists.
## Requirements
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

### Requirement: Planned-Run-Record Detection

The dedup check SHALL read each session-referenced run record and report a hit when the record's
completion state is `planned_ready_for_implementation`.

#### Scenario: Run record awaiting implementation blocks capture

- **WHEN** a run-record path appears in the transcript and that record's completion state is
  `planned_ready_for_implementation`
- **THEN** the dedup check reports that run record as a durable artifact hit

#### Scenario: Merged run record does not hit on completion state alone

- **WHEN** a referenced run record's completion state is not `planned_ready_for_implementation`
  (e.g. `completed_and_merged`)
- **THEN** the run record alone produces no dedup hit (other evidence, such as touched spec
  paths, may still produce one)

### Requirement: Merged Docs-Only Spec PR Detection Is Transcript-Local

The dedup check SHALL report a merged docs-only spec PR hit when the transcript shows both a PR
merge marker (`gh pr merge`, merged-PR reference) AND session-touched paths under `docs/specs/**`
or `openspec/changes/**`. The check SHALL NOT make network calls to GitHub.

#### Scenario: In-session spec merge detected

- **WHEN** the transcript contains a `gh pr merge` command and Edit calls under
  `docs/specs/<slug>/`
- **THEN** a merged docs-only spec PR hit is reported

#### Scenario: No network at hook time

- **WHEN** the Stop hook runs offline
- **THEN** the merged-docs-only-spec-PR detection still evaluates from transcript content alone

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

### Requirement: Fail-Open And Headless-Excluded

The dedup check SHALL fail open: a missing checker binary, non-zero exit, timeout, or unparseable
output leaves the hook's behavior exactly as if no hits were found. The hook SHALL NOT run for
headless workers, and the dedup check SHALL NOT alter when the hook fires or its sentinel
behavior.

#### Scenario: Checker binary absent

- **WHEN** `worktrail-check-durable-artifact-capture-gate` is not installed and the hook fires
- **THEN** the hook emits the unmodified instruction with no dedup block and never raises

#### Scenario: Headless worker unaffected

- **WHEN** the hook runs with headless mode enabled
- **THEN** it exits without emitting any instruction, dedup gate included

### Requirement: Capture-Time Overlap Warning

Handoff capture SHALL, before writing a new brief, scan the target repo's `docs/specs/` slug
directories, `openspec/changes/` directory names, and open PR titles against the focus text using
the consume-time cluster-detection tokenization and overlap threshold. Every candidate at or
above the threshold SHALL be surfaced as a warning; the write itself SHALL never be blocked.

#### Scenario: Focus overlaps an existing spec slug

- **WHEN** capture runs with `--focus "implement tailwind v4 migration"` and the repo's
  `docs/specs/003-tailwind-v4-migration/` exists with overlap at/above the shared threshold
- **THEN** a warning naming the spec slug is emitted alongside the created brief, and the brief
  is written

#### Scenario: Focus overlaps an open PR title

- **WHEN** the repo has an open PR whose title overlaps the focus text at/above the shared
  threshold
- **THEN** a warning naming the PR is emitted; capture proceeds

#### Scenario: Warning never blocks and never raises

- **WHEN** `gh` is missing, the remote is null, the repo path is unreadable, or any scan fails
- **THEN** capture completes normally with no warnings and a zero exit status

