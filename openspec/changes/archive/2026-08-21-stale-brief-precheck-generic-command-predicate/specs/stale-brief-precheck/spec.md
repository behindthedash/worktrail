## ADDED Requirements

### Requirement: Command Predicate Re-Check Classifies By Exit Status

A brief whose frontmatter declares the command predicate kind SHALL have each of its
`drift-findings` entries re-checked by executing that entry's captured command and classifying
the finding **solely** by the command's exit status: an exit status of `0` SHALL classify the
finding as still holding, an exit status of `1` SHALL classify it as resolved, and **any other
exit status** SHALL be treated as an error for the whole brief's re-check, per the Requirement:
Deterministic Predicate Re-Check Precedes Evidence Surfacing. The system SHALL NOT infer a
classification from the command's output text, and SHALL NOT treat a non-zero, non-`1` status —
including a status produced by a missing executable, a crash, or a timeout — as "resolved".

This polarity is fixed and not per-brief configurable: the captured command answers "does the
condition that generated this brief still hold?", exiting `0` for yes, mirroring `grep -q` and
`test`.

#### Scenario: A command that still reports the condition keeps the brief live
- **WHEN** a command-predicate brief's captured command for a finding exits `0`
- **THEN** that finding is classified as still holding

#### Scenario: A command that no longer reports the condition resolves the finding
- **WHEN** a command-predicate brief's captured command for a finding exits `1`
- **THEN** that finding is classified as resolved

#### Scenario: An unexpected exit status errors the whole re-check
- **WHEN** a captured command exits with a status other than `0` or `1` (for example `2` from a
  detector's own argument error, or `127` because the executable is not on `PATH`)
- **THEN** the predicate re-check for that brief is treated as an error, no finding is
  classified, and the dispatch falls through to the unmodified probe-based flow

#### Scenario: Output text never overrides the exit status
- **WHEN** a captured command exits `0` while printing text that reads like the condition was
  fixed
- **THEN** the finding is still classified as holding, because only the exit status is consulted

#### Scenario: A mixed brief is still-true when any finding holds
- **WHEN** a command-predicate brief captured two findings and their commands exit `1` and `0`
  respectively
- **THEN** the brief's re-check outcome is "still true", consistent with the Requirement:
  Predicate Still True Proceeds Automatically With Recorded Evidence

### Requirement: Command Predicate Execution Is Bounded And Shell-Free

Executing a captured predicate command SHALL be bounded and free of shell interpretation. The
system SHALL execute each command as an argument vector with no shell, with the working
directory pinned to the brief's repository, under a per-command wall-clock timeout, and SHALL
execute at most a bounded number of commands for any one brief. A captured command that is not a
non-empty list of strings — including a single command string — SHALL be rejected as malformed
and SHALL NOT be shell-split or executed. Every bound or validation failure SHALL classify as an
error for the whole brief's re-check, never as "resolved", so a bounded failure can only ever
cost a fall-through to the existing human-in-the-loop flow, never an automatic closure.

#### Scenario: A command string is rejected rather than shell-split
- **WHEN** a finding's captured command is a single string such as `grep -q foo bar.yml`
  rather than a list of arguments
- **THEN** it is rejected as malformed, no shell is invoked, the re-check outcome is an error,
  and the dispatch falls through to the unmodified probe-based flow

#### Scenario: Shell metacharacters are passed through as literal arguments
- **WHEN** a captured command's argument vector contains a shell metacharacter such as `;` or
  `$(...)` inside one of its arguments
- **THEN** that argument is passed to the executed program verbatim and is never interpreted by
  a shell

#### Scenario: A hanging command times out as an error
- **WHEN** a captured command does not exit within the per-command timeout
- **THEN** the command is terminated, the re-check outcome for the brief is an error, and the
  dispatch falls through to the unmodified probe-based flow

#### Scenario: A brief with too many findings is not executed
- **WHEN** a command-predicate brief carries more `drift-findings` entries than the per-brief
  execution cap allows
- **THEN** no captured command is executed, the re-check outcome is an error, and the dispatch
  falls through to the unmodified probe-based flow

#### Scenario: Commands run against the brief's repository
- **WHEN** a captured command is executed for a brief whose repository is `<repo>`
- **THEN** it runs with `<repo>` as its working directory, so a repository-relative pathspec in
  the captured command resolves against the repository the brief names

## MODIFIED Requirements

### Requirement: Deterministic Staleness Predicate Is Captured On Sweep-Generated Briefs

A brief filed by a sweep that can express its triggering condition as a deterministic,
machine-checkable predicate SHALL stamp that predicate's re-runnable inputs onto the brief as
structured frontmatter. The structured frontmatter SHALL be a `drift-findings` list with one
entry per captured hit, and each entry SHALL carry at minimum an identifying `path` relative to
the repository root. This is in addition to, and SHALL NOT replace, the existing human-readable
prose rendering of the same findings in the brief body.

A sweep SHALL express its predicate in exactly one of two forms:

- **A named predicate**, identified by the `drift-source` marker value the sweep already writes,
  whose re-check logic the system implements internally. The checkbox-drift-sweep is such a
  predicate.
- **A command predicate**, declared by a `predicate-kind: command` frontmatter field, where each
  `drift-findings` entry additionally carries a `predicate-cmd` argument vector — the exact
  command that re-derives whether that finding still holds. A command predicate SHALL NOT be
  required to change its `drift-source` value: `drift-source` remains the filing sweep's own
  identity (and the key the brief-dedup path and every recorded evidence line use), while
  `predicate-kind` selects the re-check mechanism. This form exists so a sweep whose detector
  lives outside this codebase — in another repository, or in another language — can register its
  briefs for automatic re-verification without that detector being importable.

#### Scenario: A checkbox-drift brief carries structured, re-runnable findings
- **WHEN** the checkbox-drift-sweep files a brief for one or more `status: completed` task
  files with unchecked body checkboxes
- **THEN** the brief's frontmatter includes a `drift-findings` list with each affected task
  file's path, alongside the existing `drift-source: checkbox-drift-sweep` marker and the
  existing prose bullet list in the body

#### Scenario: An external sweep captures a re-runnable command per finding
- **WHEN** a sweep whose detector this codebase cannot import files a brief for one or more hits
- **THEN** the brief's frontmatter carries `predicate-kind: command`, its own `drift-source`
  value naming the sweep, and a `drift-findings` list whose entries each carry a `path` and the
  `predicate-cmd` argument vector that re-derives that finding

#### Scenario: Missing structured findings is treated as no predicate
- **WHEN** a brief carries `drift-source: checkbox-drift-sweep` but no `drift-findings`
  frontmatter (for example, a brief captured before this requirement existed)
- **THEN** a predicate re-check attempted against that brief cannot determine an outcome and the
  dispatch falls through to the unmodified probe-based staleness flow

#### Scenario: A command-kind brief without captured commands is treated as an error
- **WHEN** a brief carries `predicate-kind: command` but one or more of its `drift-findings`
  entries carries no `predicate-cmd`
- **THEN** the predicate re-check for that brief is treated as an error and the dispatch falls
  through to the unmodified probe-based staleness flow

### Requirement: Deterministic Predicate Re-Check Precedes Evidence Surfacing

For every brief-sourced dispatch, before the probe-based staleness search
(`check_brief_staleness`) runs or any evidence is surfaced to the operator, the system SHALL
check whether the claimed brief carries a predicate it can re-run and a non-empty
`drift-findings` list. Predicate selection SHALL be deterministic and SHALL resolve in this
order: a named predicate registered for the brief's `drift-source` value takes precedence;
otherwise a brief declaring `predicate-kind: command` dispatches to the command predicate;
otherwise no predicate is recognized. When a predicate is recognized and `drift-findings` is
non-empty, the system SHALL re-run that predicate's check against the current base branch,
restricted to the captured `drift-findings` entries, and SHALL use the outcome of that re-check
to decide the dispatch, bypassing the probe-based search and operator prompt entirely. When no
predicate is recognized, `drift-findings` is absent or empty, or the recognized predicate's
re-check itself fails (an unreadable or unparseable task file, a malformed or non-executable
captured command, an unexpected exit status, a timeout, or any other error), the system SHALL
fall through unchanged to today's probe-based `check_brief_staleness` search and operator-prompt
flow, exactly as if no predicate had been attempted.

#### Scenario: Recognized predicate with captured findings runs first
- **WHEN** a claimed brief carries `drift-source: checkbox-drift-sweep` and a non-empty
  `drift-findings` list
- **THEN** the predicate re-check runs before `check_brief_staleness` and before any operator
  prompt, and its outcome — not the probe-based search — decides the dispatch

#### Scenario: A named predicate takes precedence over the command predicate
- **WHEN** a claimed brief carries both a `drift-source` value with a named predicate
  registered for it and a `predicate-kind: command` declaration
- **THEN** the named predicate runs and no captured command is executed

#### Scenario: A command-kind brief with an unregistered drift-source still re-checks
- **WHEN** a claimed brief carries `predicate-kind: command`, a non-empty `drift-findings` list
  with a `predicate-cmd` per entry, and a `drift-source` value for which no named predicate is
  registered
- **THEN** the command predicate re-check runs before `check_brief_staleness` and before any
  operator prompt, and its outcome decides the dispatch

#### Scenario: No drift-source falls through unchanged
- **WHEN** a claimed brief carries no `drift-source` frontmatter field
- **THEN** no predicate re-check is attempted and the dispatch proceeds through the unmodified
  probe-based `check_brief_staleness` search and operator-prompt flow

#### Scenario: Unrecognized drift-source falls through unchanged
- **WHEN** a claimed brief carries a `drift-source` value for which no named predicate is
  registered, and does not declare `predicate-kind: command`
- **THEN** no predicate re-check is attempted for that value and the dispatch proceeds through
  the unmodified probe-based flow

#### Scenario: A re-check error falls through unchanged
- **WHEN** a recognized predicate's re-check raises, cannot read one of its captured
  `drift-findings` entries (for example, the task file no longer exists at the captured path),
  or cannot classify one of its captured commands
- **THEN** the predicate re-check outcome is treated as inconclusive, no auto-proceed or
  auto-close occurs on its basis, and the dispatch falls through to the unmodified probe-based
  `check_brief_staleness` search and operator-prompt flow

### Requirement: Predicate Still True Proceeds Automatically With Recorded Evidence

When a predicate re-check completes without error and finds that the condition which generated
the brief still holds for at least one captured finding, the system SHALL proceed with the
dispatch automatically, without prompting the operator, and SHALL record the predicate
re-check's result — including which findings still hold — on the run record once it is opened.
The system SHALL NOT omit this recording; a predicate re-check outcome of "still true" SHALL
always be recorded, never silently skipped.

When the predicate was command-based, the recorded evidence SHALL additionally include the
re-run transcript: for each classified finding, the command that was executed and the exit
status it returned, together with a bounded excerpt of its output. Naming the sweep alone is not
sufficient evidence for a command predicate, because the executed command is the only thing that
makes the classification checkable after the fact.

#### Scenario: A still-drifted finding proceeds without a prompt
- **WHEN** a checkbox-drift brief's predicate re-check finds that one of its captured task
  files is still `status: completed` with an unchecked body checkbox
- **THEN** the dispatch proceeds to Phase 6/7 without an operator prompt

#### Scenario: A partially-resolved brief is still treated as true
- **WHEN** a checkbox-drift brief captured findings for two task files, and the re-check finds
  one file's checkboxes now fully checked but the other file still `status: completed` with an
  unchecked checkbox
- **THEN** the predicate re-check outcome is "still true" (proceed), because the condition that
  generated the brief — drift exists in this repository's captured scope — still holds

#### Scenario: The still-true outcome is recorded on the run record
- **WHEN** the predicate re-check outcome is "still true" and Phase 6 opens the run record for
  the proceeding dispatch
- **THEN** the run record carries an entry naming the predicate re-check and the findings that
  still hold, and this entry is never omitted

#### Scenario: A command predicate's still-true evidence shows the re-run transcript
- **WHEN** a command-predicate brief's re-check outcome is "still true" and Phase 6 opens the
  run record
- **THEN** the recorded entry includes, per classified finding, the executed command, its exit
  status, and a bounded excerpt of its output, in addition to naming the sweep and the findings
  that still hold

### Requirement: Predicate Resolved Closes The Brief Automatically Citing The Re-Check

When a predicate re-check completes without error and finds that the condition which generated
the brief no longer holds for any captured finding, the system SHALL close the brief
automatically as already-delivered through the work-queue owner, before Phase 6 opens a run
record, citing the predicate re-check result itself as the reason for closure. The system SHALL
NOT cite matched commits or merged pull requests as the reason for this closure, because such
matches are not computed for a brief resolved this way and, even when coincidentally available,
are not proof that the predicate's own condition was what changed.

When the predicate was command-based, the closure note SHALL additionally show the re-run
transcript — the executed command, its exit status, and a bounded excerpt of its output, for
each resolved finding — so the closure carries shown evidence rather than an asserted
re-verification claim, and SHALL therefore be accepted by the work-queue owner's closure
evidence gate rather than rejected by it.

#### Scenario: All captured findings resolved closes the brief
- **WHEN** a checkbox-drift brief's predicate re-check finds that every captured task file is
  either no longer `status: completed` or has every audited checkbox now checked
- **THEN** the brief is closed as already-delivered automatically, with no operator prompt and
  no dispatch to Phase 6/7

#### Scenario: The closure note cites the predicate re-check, not commit/PR evidence
- **WHEN** the system closes a brief under this requirement
- **THEN** the closure note names the predicate re-check (which findings were re-verified as
  resolved) and does not name or rely on any matched commit SHA or pull-request number as the
  reason for closure

#### Scenario: A command predicate's closure note shows the re-run transcript
- **WHEN** the system closes a command-predicate brief under this requirement
- **THEN** the closure note shows, per resolved finding, the executed command, its exit status,
  and a bounded excerpt of its output

#### Scenario: The generated closure note passes the closure evidence gate
- **WHEN** a command-predicate closure note generated under this requirement is submitted to the
  work-queue owner's completion path
- **THEN** the closure is accepted, and is never rejected as an unverified re-verification claim
