# worker-fold-in-policy Specification

## Purpose

Let a run fix a verified, mechanical defect in a file it is already editing — in the same
change and PR — instead of paying it out as a separate work-queue brief, while keeping the
declared-scope discipline that makes parallel fan-out safe: fold-ins live strictly inside the
scope a unit already declared, are declared back to the orchestrator, and are validated in
code before they can be accepted.

## ADDED Requirements

### Requirement: Implement and fix worker prompts carry the fold-in clause

The worker prompts for the implement and fix roles SHALL include a fold-in clause that permits
exactly one bounded extension of a task's work: a defect located in a file already named in
the task's Scope may be folded in when all of the following hold — the defect is verified
(reproduced or directly evidenced; never a hypothesis or a "while I'm here" cleanup), the fix
is mechanical (it restores documented or established intent and introduces no new design, API,
or behavior contract; behavior-contract changes are out of scope for a fold-in), and the task
stays within the fold-in caps of at most 2 fold-ins and about 20 changed lines. The clause
SHALL require each fold-in to be committed separately from the task's own work and listed in
the report-back, and SHALL direct everything else — unverified findings, files outside Scope,
non-mechanical fixes, and over-cap findings — to be reported in `notes` for capture rather
than fixed. The clause SHALL NOT relax the standing scope rule ("Touch no files outside
scope"); the clean role's instructions SHALL NOT gain the clause and SHALL otherwise be
unchanged.

#### Scenario: Implement prompt states the fold-in clause and its conditions

- **WHEN** the implement worker prompt is rendered for a task with declared scope
- **THEN** it contains a fold-in clause naming the verified, mechanical, and in-scope
  conditions, the separate-commit requirement, the caps (2 fold-ins / ~20 changed lines), and
  the report-in-notes fallback for everything that does not qualify

#### Scenario: Fix prompt states the same clause

- **WHEN** the fix worker prompt is rendered
- **THEN** it contains the fold-in clause with the same conditions, commit requirement, caps,
  and fallback

#### Scenario: Clean role is not widened

- **WHEN** the clean worker prompt is rendered
- **THEN** it contains no fold-in clause and its instructions are otherwise unchanged

### Requirement: Report-back declares fold-ins through an optional `fold_ins` field

The report-back contract SHALL accept an optional `fold_ins` array whose entries are shaped
`{"file": "<repo-relative path>", "commit": "<sha>", "summary": "<one line>"}`. A report-back
that omits the field — or carries an empty array — SHALL parse exactly as before this change:
an absent key means zero fold-ins and is never an error. When entries are present, each SHALL
be recorded on that task's run-journal entry so the declaration survives the run and is
auditable without re-reading worker output.

#### Scenario: Fold-ins are recorded on the journal entry

- **WHEN** an implement or fix report-back carrying `fold_ins` entries is applied
- **THEN** each entry's file, commit, and summary appear on that task's run-journal entry

#### Scenario: A report without the key parses exactly as before

- **WHEN** a report-back with no `fold_ins` key is parsed and applied
- **THEN** it parses without error and its journal entry is byte-identical to today's — no
  `fold_ins` field appears anywhere on it

#### Scenario: An empty array is equivalent to an absent key

- **WHEN** a report-back carries `"fold_ins": []`
- **THEN** it behaves exactly like a report-back with no `fold_ins` key

### Requirement: Declared fold-ins are validated in code against the task's declared scope and fail the task closed

The orchestrator SHALL validate every `fold_ins[].file` against the task's declared `files:`
scope, comparing repo-relative paths under the same normalization the orchestrator already
applies to declared-scope checks. An entry naming a file outside the declared scope, or an
entry whose `file` cannot be read as a path (missing, blank, or not a string), SHALL fail the
task through the same terminal path as a failed review: the report SHALL NOT be accepted as
success, the task SHALL be driven to its terminal failed state with the journal entry carrying
the same terminal-classification stamp other terminal failures carry (so the clearing and
quarantine surfaces treat it identically), and the offending entries SHALL be recorded on the
entry for audit. Valid in-scope entries SHALL NOT alter the report's transition — a report
with only valid fold-ins proceeds exactly as a report with none. This validation SHALL be
code-enforced and independent of whether a reviewer runs.

#### Scenario: An out-of-scope fold-in fails the task closed

- **WHEN** a synthetic implement report-back declares a `fold_ins` entry naming a file outside
  the task's declared scope
- **THEN** the report is not accepted as success, the task ends in its terminal failed state
  with the entry carrying the same terminal stamp other terminal failures get, and the
  offending entry is recorded on the journal entry

#### Scenario: A malformed fold-in entry fails closed

- **WHEN** a report-back's `fold_ins` entry has a missing, blank, or non-string `file`
- **THEN** the same closed-failure behavior applies — the entry cannot be validated and is
  never silently accepted

#### Scenario: Valid in-scope fold-ins leave the transition unchanged

- **WHEN** an implement report-back declares fold-ins whose files are all members of the
  task's declared scope
- **THEN** the task transitions exactly as a report-back without fold-ins would (implement to
  review), and the fold-ins are recorded

### Requirement: Reviewer validates declared fold-ins while undeclared drift still fails

The review instructions given to review workers SHALL direct the reviewer to validate each
declared fold-in — the file is inside the task's declared scope, the change is mechanical
(restores documented or established intent, no new design, API, or behavior contract), the
task stays within the fold-in caps, and the task's tests still pass — and to FAIL the review
when a declared fold-in does not meet those conditions. The same instructions SHALL continue
to require FAILING undeclared out-of-scope edits and unexplained drift exactly as before:
a declared, validated fold-in is not scope drift, and nothing else about the reviewer's drift
duty changes.

#### Scenario: Review prompt carries the validation duty

- **WHEN** the review worker prompt/instructions are rendered
- **THEN** they instruct the reviewer to validate each declared fold-in against the
  in-scope, mechanical, caps, and tests-still-pass conditions and to fail the review when one
  does not meet them

#### Scenario: Undeclared out-of-scope edits still fail

- **WHEN** a diff contains an out-of-scope or unexplained change that was not declared as a
  fold-in
- **THEN** the reviewer's existing drift-failure behavior applies unchanged

#### Scenario: A non-qualifying declared fold-in fails review

- **WHEN** a declared fold-in is in scope but not mechanical (it introduces a new behavior
  contract) or leaves tests failing
- **THEN** the review FAILS on that finding and the task enters the normal fix loop

### Requirement: Authoring stage folds in adjacent defects in the change's own file surface

The SDD workflow doctrine SHALL define the three decision tiers for a defect found mid-run —
tier 1 fold in (same change, under the conditions and caps above), tier 2 capture the brief
and let the existing `fold-into-change` triage route it into a change, tier 3 a standalone
brief for everything else — and SHALL direct the authoring stage (propose/plan authoring) to
fold in a found defect whose fix touches only files already in the change's own file surface,
by extending a task's declared scope or adding a task, instead of capturing a work-queue
brief. The doctrine SHALL NOT introduce new triage verdict vocabulary; tier 2 reuses
`fold-into-change` exactly as it exists.

#### Scenario: The three-tier doctrine is present in the workflow skill

- **WHEN** the SDD workflow skill's prose is inspected
- **THEN** it documents all three tiers, the tier-1 conditions and caps, and the
  authoring-stage rule to extend task scope or add a task when the defect's file is already in
  the change's file surface

#### Scenario: No new triage vocabulary

- **WHEN** the doctrine describes tier 2
- **THEN** it names the existing `fold-into-change` verdict and introduces no new verdict or
  route names
