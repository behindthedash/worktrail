## Purpose

A mechanical dedup gate on Worktrail's follow-up capture path: before the Stop hook instructs an
agent to auto-capture a handoff, and before handoff capture writes a brief, the system checks
whether a durable artifact (session-touched spec/OpenSpec change, run record finishing
`planned_ready_for_implementation`, merged docs-only spec PR, spec-slug or open-PR overlap)
already tracks the same follow-up, and downgrades or warns instead of duplicating work onto two
lists.

## ADDED Requirements

### Requirement: Session-Touched Durable-Artifact Detection

The Stop hook SHALL detect, from its existing single transcript pass, every file path the session
touched via edit/write tools or Bash redirects that falls under `docs/specs/**` or
`openspec/changes/**`, and treat each as a durable artifact tracking this session's work.

#### Scenario: Edited OpenSpec change triggers detection

- **WHEN** the session transcript shows an Edit tool call whose `file_path` is under
  `openspec/changes/<name>/`
- **THEN** the hook reports `<name>` as a session-touched durable artifact

#### Scenario: Read-only spec access does not trigger detection

- **WHEN** the session only read spec files (no edit/write tool calls or Bash writes touching
  `docs/specs/**` or `openspec/changes/**`)
- **THEN** no session-touched durable artifact is reported

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
auto-capture: it SHALL name the matched durable artifacts, forbid capturing a brief for work they
track, and require instead a suggestion-only line naming the resume command. The instruction
SHALL permit capture only when the agent carries explicit justification, which MUST be embedded
in the captured brief itself as a dedup-justification section naming the tracked artifact and why
a separate brief is warranted.

#### Scenario: Hit downgrades to suggestion-only

- **WHEN** the session touched `openspec/changes/003-tailwind-v4-migration/` and the hook fires
- **THEN** the printed instruction names that change, forbids auto-capturing a brief for work it
  tracks, and requires a suggestion-only line with the resume command

#### Scenario: Explicit justification escape hatch is stated in the instruction

- **WHEN** the dedup gate block is emitted
- **THEN** the block states the only escape hatch: capture requires explicit justification
  recorded as a dedup-justification section inside the brief text

#### Scenario: No hit leaves the instruction unchanged

- **WHEN** the dedup check reports no hits
- **THEN** the hook's instruction is identical to the pre-gate instruction (EXCEPTIONAL-VALUE
  gate plus any deferred-work block), byte-for-byte

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
