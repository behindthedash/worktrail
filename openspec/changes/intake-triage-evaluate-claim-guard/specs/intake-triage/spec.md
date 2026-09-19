## ADDED Requirements

### Requirement: Interactive single-brief pickup refuses a brief owned by another claimant
Before evaluating or applying a verdict for a single intake brief, the system SHALL
re-resolve the brief by id across `queue/` and then `picked/` rather than trusting the path
it was handed. When the brief is found in `picked/` with a `claimed-by` stamp, or is
`status: done`, the single-brief evaluate command SHALL NOT spawn an evaluator and the
single-brief apply command SHALL NOT write to the brief: both SHALL print `null`, write a
`blocked_brief_owned: <brief-id> owned by <claimed-by> (claimed-at <timestamp>)` line to
stderr, and exit 2. When the id resolves in neither folder, they SHALL print `null`, write
`blocked_brief_missing: <brief-id>`, and exit 2. Applying a `keep` or `needs-update`
verdict through the shared apply path SHALL likewise refuse a brief found in `picked/`
with a `claimed-by` stamp, returning a `status: error` action-log entry whose `error`
names the owner and leaving the brief unmodified. The `worktrail-go` skill text SHALL
document both exits as cases that stop the gate without proceeding to apply.

#### Scenario: Evaluate refuses a brief claimed by a scheduled run
- **WHEN** `--evaluate-brief-triage queue/<id>.md` is run after a queue-triage run has
  moved that brief to `picked/` with `claimed-by: queue-triage`
- **THEN** no evaluator is spawned, the command prints `null`, writes
  `blocked_brief_owned: <id> owned by queue-triage (claimed-at <ts>)` to stderr, and
  exits 2 with the picked brief byte-for-byte unchanged

#### Scenario: Apply refuses a brief claimed between evaluate and apply
- **WHEN** a `keep` verdict file was produced while the brief sat in `queue/`, and
  `--apply-brief-triage-file <path> --confirm` runs after another owner claimed it
- **THEN** no triage note is appended, the command prints `null`, writes the
  `blocked_brief_owned` line, and exits 2

#### Scenario: Shared apply path refuses a claimed brief on needs-update
- **WHEN** `apply_verdicts()` processes a `needs-update` verdict whose brief is in
  `picked/` with `claimed-by: queue-triage`
- **THEN** the returned entry has `status: error` and an `error` naming `queue-triage`,
  and the brief is unmodified

#### Scenario: Unclaimed brief in queue/ is unaffected
- **WHEN** `--evaluate-brief-triage queue/<id>.md` is run for a brief still in `queue/`
- **THEN** evaluation proceeds exactly as before this change

#### Scenario: Brief id resolves nowhere
- **WHEN** `--evaluate-brief-triage queue/<id>.md` is run and `<id>` exists in neither
  `queue/` nor `picked/`
- **THEN** the command prints `null`, writes `blocked_brief_missing: <id>` to stderr, and
  exits 2

### Requirement: Interactive single-brief evaluate fails loud on an empty brief
When the single-brief evaluate resolves a brief whose file cannot be read, or whose
`focus:` frontmatter and `## Focus` body section are both empty, the system SHALL NOT
spawn an evaluator or produce a verdict. It SHALL print `null`, write a
`blocked_empty_brief: <brief-id> (<reason>)` line to stderr, and exit 2. The lenient
empty-string fallback SHALL remain for the whole-queue evaluate path.

#### Scenario: Brief with no focus text is refused
- **WHEN** `--evaluate-brief-triage` is run for a brief whose frontmatter has no `focus:`
  and whose body has no `## Focus` section
- **THEN** no evaluator is spawned, the command prints `null`, writes
  `blocked_empty_brief: <id> (no focus text)` to stderr, and exits 2

#### Scenario: Unreadable brief is refused
- **WHEN** the resolved brief file raises `OSError` on read
- **THEN** the command prints `null`, writes `blocked_empty_brief: <id> (unreadable: ...)`
  to stderr, and exits 2 rather than evaluating an empty prompt

#### Scenario: Whole-queue evaluate keeps the lenient reader
- **WHEN** `group_queue_by_repo()` reads the focus of a brief that became unreadable
- **THEN** it still receives an empty string and does not raise

### Requirement: Bare brief id resolves against picked/ as well as queue/
When `worktrail-go-parse` classifies a single bare or prefix token as a brief id and
`queue/` yields no match, the system SHALL retry the resolution against the sibling
`picked/` folder. A match there SHALL produce `mode: brief` with `brief_status: picked`,
the picked brief's path, and its `claimed_by` value, so the go skill can report
`owned by <claimed-by>` and stop without an evaluate call. An `ambiguous` result from
`queue/` SHALL NOT be retried against `picked/`. A token matching neither folder SHALL
fall through to free text exactly as before.

#### Scenario: Claimed brief id no longer falls through to free text
- **WHEN** `worktrail-go-parse 20260918-154644` is run and the only matching brief is
  `picked/20260918-154644-claude-target-write-agents-md.md` with `claimed-by: queue-triage`
- **THEN** the result has `mode: brief`, `brief_status: picked`, the picked path, and
  `claimed_by: queue-triage`

#### Scenario: Unknown token still falls through
- **WHEN** `worktrail-go-parse 20990101-000000` is run and neither folder matches
- **THEN** the result is `mode: free_text` with reason
  `bare or prefix brief id did not resolve (none) -- free text`

#### Scenario: Skill text stops on a picked resolution
- **WHEN** the Phase 2 intake-brief triage gate text of `skills/worktrail-go/SKILL.md` is read
- **THEN** it instructs the session to print `owned by <claimed-by>` and stop when the
  parse result's `brief_status` is `picked`, and lists `blocked_brief_owned`,
  `blocked_brief_missing`, and `blocked_empty_brief` next to `blocked_pending_decision`
  as exit-2 cases that do not proceed to apply
