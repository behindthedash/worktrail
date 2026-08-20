# stale-brief-precheck Specification

## Purpose
TBD - created by archiving change stale-brief-precheck. Update Purpose after archive.
## Requirements
### Requirement: Evidence Probe Extraction From Brief Text

The system SHALL extract three kinds of Evidence Probe from a brief's focus text: **path
probes** (tokens shaped like a file path or a bare filename with an extension), **symbol
probes** (code-identifier-shaped tokens, including CLI-flag-shaped tokens), and **pull-request
probes** (explicit pull-request references). Extraction SHALL be purely textual and SHALL NOT
consult the repository.

Backtick-quoted tokens SHALL be preferred as probe sources. A token SHALL qualify as a path
probe when it contains a `/` separator **or** ends in a file extension of one to ten
characters; a bare filename with an extension therefore qualifies without needing a directory
component. An unquoted token SHALL additionally qualify as a **symbol** probe when it is
distinctively identifier-shaped — snake_case, with letters on both sides of an underscore — or
when it is a GNU-long-form **CLI-flag**-shaped token (`--` followed by a letter, then letters,
digits, and single hyphens) — because briefs captured through the primary capture path contain
no backticks at all. A backtick-quoted token SHALL qualify as a symbol probe under the same two
rules (identifier-shaped or CLI-flag-shaped) in addition to the plain-identifier rule already in
effect for backtick-quoted tokens.

A token SHALL NOT qualify as a path probe when it is an absolute or home-relative path (it
names something outside the repository being searched), when it contains parentheses or angle
brackets (a prose call-site list or task chain, not a pathspec), when its apparent path
structure carries no letters (a task id such as `1.1` or `2.1/2.2/2.3`, which is the single
most common token shape in a brief), or when its stripped, lowercased form exactly matches a
fixed denylist of common non-path prose abbreviations (`e.g`, `i.e`, `etc`, `vs`, `a.k.a`) —
these abbreviations end in a genuine dot-plus-letters sequence (`.g`, `.e`, `.a`) that is
indistinguishable in shape from a legitimate one- or two-character file extension, but they name
no file and MUST NOT be searched as path probes regardless of what the extension-shape rule
above would otherwise admit.

Bare-filename path probes SHALL be searched with a pathspec that matches the file at the
repository root as well as nested beneath it.

#### Scenario: Bare filename with an extension is a path probe
- **WHEN** a brief's focus text contains the backtick-quoted token `prevent-destructive-commands.py`
- **THEN** `prevent-destructive-commands.py` is extracted as a path probe, even though it
  contains no `/` separator

#### Scenario: A bare-filename probe matches at the repository root
- **WHEN** a bare-filename path probe names a file that lives at the repository root
- **THEN** commits touching that root file are reported, not only commits touching a
  same-named file nested in a subdirectory

#### Scenario: Dotted and underscored identifiers are symbol probes
- **WHEN** a brief's focus text contains the backtick-quoted tokens `_task_files_are_shipped`
  and `resolve_routing`
- **THEN** both are extracted as symbol probes

#### Scenario: A call-suffixed identifier is extracted as the bare symbol
- **WHEN** a brief's focus text contains `compile_run_plan()`
- **THEN** `compile_run_plan` is extracted as a symbol probe, with the call parentheses removed

#### Scenario: Unquoted snake_case identifiers are symbol probes
- **WHEN** a brief's focus text is unbackticked prose containing `compile_run_plan` and
  `apply_to_tasks`
- **THEN** both are extracted as symbol probes

#### Scenario: A backtick-quoted CLI flag is a symbol probe
- **WHEN** a brief's focus text contains the backtick-quoted token `--tier-map`
- **THEN** `--tier-map` is extracted as a symbol probe

#### Scenario: An unquoted CLI flag is a symbol probe
- **WHEN** a brief's focus text is unbackticked prose containing "add the --json flag"
- **THEN** `--json` is extracted as a symbol probe

#### Scenario: Ordinary unquoted prose is not a symbol probe
- **WHEN** a brief's focus text contains ordinary words and hyphenated words such as
  "resolve the base ref" and "file-scope"
- **THEN** none of them are extracted as symbol probes

#### Scenario: Task ids and absolute paths are not path probes
- **WHEN** a brief's focus text contains `1.1`, `2.1/2.2/2.3/2.4`, and a `repo:` line naming an
  absolute path
- **THEN** none of them are extracted as path probes

#### Scenario: Common prose abbreviations are not path probes
- **WHEN** a brief's focus text contains the unbackticked prose "see e.g. the router", "i.e. the
  same module", and "a.k.a. the guard"
- **THEN** none of `e.g`, `i.e`, or `a.k.a` are extracted as path probes, even though each looks
  path-shaped after trailing punctuation is stripped

#### Scenario: Legitimate short extensions are still path probes
- **WHEN** a brief's focus text contains the backtick-quoted tokens `guard.py`, `README.md`, and
  `deploy.sh`
- **THEN** all three are extracted as path probes, unaffected by the abbreviation denylist

#### Scenario: Pull-request references are extracted with their number
- **WHEN** a brief's focus text contains `devops PR #89` and `behindthedash/devops#89`
- **THEN** pull-request probe `89` is extracted, deduplicated to a single entry

#### Scenario: Prose without code-shaped tokens yields no probes
- **WHEN** a brief's focus text is ordinary prose containing no backtick-quoted tokens, no
  path-shaped tokens, no CLI-flag-shaped tokens, and no pull-request references
- **THEN** all three probe lists are empty and the check reports no evidence

### Requirement: Probe Count Is Bounded

The system SHALL cap the total number of probes it searches, per probe kind, at a documented
maximum, and SHALL apply a per-invocation timeout to every subprocess it runs. When extraction
yields more candidates than the cap, the system SHALL retain the most specific candidates
(longer, more distinctive tokens before shorter, more generic ones) and SHALL report the
number of candidates dropped.

#### Scenario: Excess probes are truncated and the drop is reported
- **WHEN** extraction yields more path probes than the configured cap
- **THEN** only the cap-many most specific probes are searched, and the result reports the
  count of dropped candidates rather than silently discarding them

#### Scenario: A hanging subprocess does not hang the dispatch
- **WHEN** a `git` invocation exceeds the per-invocation timeout
- **THEN** that probe contributes no matches, the result carries a warning naming the timeout,
  and the check still returns

### Requirement: History Search Is Bounded By The Brief's Capture Time

The system SHALL search the repository's base-branch history for changes matching each probe,
restricted to commits authored at or after a **search boundary** computed as the brief's
`created:` timestamp minus a fixed, documented grace period (`RACE_GRACE_SECONDS`). The grace
period exists to catch a delivering commit that lands on the base branch moments before the
brief describing the same work is captured, in the same session — a same-session race that an
exact-timestamp boundary would otherwise miss entirely. Path probes SHALL be searched by path;
symbol probes (including CLI-flag-shaped probes) SHALL be searched **both** by
change-in-occurrence-count (`git log -S`) and by commit message (`git log --grep`), since a
commit that moved, reverted, or merely described the work can name the symbol in its subject
without changing its occurrence count. Each reported match SHALL carry the kind of search that
found it, and a commit found by more than one search for the same probe SHALL be reported once.
The base branch SHALL be resolved preferring the remote-tracking ref when one exists, so the
search sees work that landed upstream but has not been merged into the local checkout.

#### Scenario: A commit landing after capture is reported as evidence
- **WHEN** a brief was captured on 2026-07-31 and a commit touching one of its path probes
  landed on the base branch on 2026-08-02
- **THEN** that commit is reported as a match, carrying its short SHA, commit date, and subject

#### Scenario: A commit landing moments before capture is reported as evidence
- **WHEN** a brief's `created:` timestamp is `T`, and a commit touching one of its probes
  landed on the base branch at `T` minus 56 seconds — well inside the grace period
- **THEN** that commit is reported as a match, not silently excluded

#### Scenario: A commit predating capture is not evidence
- **WHEN** the only commit touching a probe landed before `T` minus `RACE_GRACE_SECONDS`, the
  grace-widened search boundary
- **THEN** it is not reported as a match, because work that far outside the search boundary
  cannot be what the brief was filed against

#### Scenario: A commit naming a symbol only in its message is found
- **WHEN** a commit's diff does not change a symbol probe's occurrence count but its subject
  names that symbol
- **THEN** the commit is reported as a match, distinguished from an occurrence-count match by
  its recorded search kind

#### Scenario: A commit found by both searches is reported once
- **WHEN** a commit both changes a symbol probe's occurrence count and names it in its subject
- **THEN** exactly one match is reported for that commit and probe

#### Scenario: Remote-tracking ref is preferred over the local branch
- **WHEN** the local base branch is behind its remote-tracking ref and the delivering commit
  exists only on the remote-tracking ref
- **THEN** the search still finds that commit

### Requirement: Merged Pull-Request Lookup Is Best-Effort

The system SHALL attempt to resolve pull-request probes and probe-matching merged pull requests
via the GitHub CLI, and SHALL treat the absence, failure, non-authentication, or timeout of
that CLI as "no signal" rather than as an error or as absence of evidence. The exclusion of
resolved pull requests merged before the search window SHALL use the same grace-widened search
boundary (`created:` timestamp minus `RACE_GRACE_SECONDS`) as the base-branch history search, so
a pull request that merges moments before the brief is captured is not excluded by a stricter
boundary than the one applied to commits.

#### Scenario: GitHub CLI unavailable degrades without failing
- **WHEN** `gh` is not installed, not authenticated, or times out
- **THEN** the pull-request section of the result is empty, a warning names the cause, and any
  evidence already found by the git history search is still reported

#### Scenario: A pull request merged moments before capture is not excluded
- **WHEN** a brief's `created:` timestamp is `T`, and a resolved pull request merged at `T`
  minus 56 seconds — well inside the grace period
- **THEN** that pull request is kept in the result, not excluded by the merged-before-search-
  window filter

#### Scenario: A pull request merged well before the grace-widened boundary is excluded
- **WHEN** a resolved pull request merged before `T` minus `RACE_GRACE_SECONDS`
- **THEN** it is excluded from the result and counted in the warning, exactly as a pull request
  merged long before the brief was captured is excluded today

### Requirement: The Check Fails Open

The system SHALL never raise to its caller and SHALL never block a dispatch. Any condition
under which the question cannot be answered — a path that is not a git repository, an
unreadable or unparseable brief, a missing or malformed `created:` timestamp, a git failure, a
timeout, or an empty probe set — SHALL yield a result whose `checked` field is `false`, with a
non-null warning. Callers SHALL treat `checked: false` as "no signal" and MUST NOT treat it as
"no evidence of prior delivery".

#### Scenario: Non-git path yields checked false, not an exception
- **WHEN** the check is invoked against a directory that is not a git repository
- **THEN** it returns `checked: false` with a warning, and raises nothing

#### Scenario: A malformed created timestamp does not abort the check
- **WHEN** a brief's `created:` frontmatter is missing or cannot be parsed as a timestamp
- **THEN** the check returns `checked: false` with a warning naming the unparseable value

#### Scenario: No matching evidence is a definite negative, not an error
- **WHEN** probes were extracted and searched successfully and none matched any commit
- **THEN** the result reports `checked: true` with no matches, distinguishing a searched-and-clean
  brief from an unanswerable one

### Requirement: Staleness Check Covers Every Brief-Sourced Dispatch

The system SHALL run the staleness check during `/go` Phase 5.5 whenever the dispatch is
brief-sourced, regardless of the resolved route (`A`-`J`). A free-text dispatch with no claimed
brief SHALL skip the staleness check entirely, because there is no `created:` timestamp to
bound the search and no captured prose to extract probes from, and SHALL be neither delayed nor
otherwise modified by it. The existing route `C`/`D` spec-collision branch and the route-gated
related-brief-collision branch SHALL continue to run independently of the staleness check: none
of the three branches suppresses, gates, or alters another, and more than one branch MAY run for
a single dispatch.

#### Scenario: Brief-sourced Route F dispatch runs the check
- **WHEN** a claimed brief is dispatched and Phase 5 resolves route `F`
- **THEN** the staleness check runs before Phase 6 opens the run record

#### Scenario: Free-text Route F dispatch skips the check
- **WHEN** a free-text request with no claimed brief resolves to route `F`
- **THEN** the staleness check does not run, because there is no `created:` timestamp to bound
  the search and no captured brief that could have gone stale

#### Scenario: Brief-sourced Route C dispatch runs both checks
- **WHEN** a claimed brief is dispatched and Phase 5 resolves route `C`
- **THEN** the staleness check runs before Phase 6 opens the run record, and the spec-collision
  check also runs for the same dispatch

#### Scenario: Brief-sourced Route J dispatch runs the check
- **WHEN** a claimed brief is dispatched and Phase 5 resolves route `J`
- **THEN** the staleness check runs before Phase 6 opens the run record, even though route `J`
  is covered by neither the spec-collision nor the related-brief-collision branch

### Requirement: Evidence Is Surfaced To The Operator, Never Auto-Applied

When the check reports evidence, the system SHALL present the matching commits and pull
requests to the operator and SHALL require an explicit operator decision before either closing
the brief or continuing the dispatch, **except** in either of two carve-outs:

1. **A deterministic staleness predicate re-check** (see Requirement: Deterministic Predicate
   Re-Check Precedes Evidence Surfacing) has already determined the outcome for the brief — in
   that case the probe-based evidence this requirement governs is never computed or surfaced,
   and this requirement's operator-decision gate does not apply to that brief.
2. **A file-state verification step** (see Requirement: File-State Verification Precedes
   Evidence Surfacing) has classified the probe-based evidence as verifiably absent or
   verifiably present against current file state — in that case the dispatch proceeds or the
   brief closes automatically per that verification outcome, and this requirement's
   operator-decision gate does not apply to that brief.

The system SHALL NOT close, stamp, move, or otherwise mutate the brief on the basis of this
check alone, except via one of those two carve-outs. Brief lifecycle mutations SHALL continue
to be performed only through the work-queue owner, and only as the result of an explicit
operator choice, an automatic predicate re-check outcome, or an automatic file-state
verification outcome.

#### Scenario: Evidence prompts the operator rather than closing the brief
- **WHEN** the check reports one or more matching commits for a claimed Route-F brief
- **THEN** the operator is shown the evidence and asked whether the brief is already delivered,
  and no queue mutation occurs until that answer is given

#### Scenario: Operator may proceed despite evidence
- **WHEN** the operator judges the surfaced evidence to be unrelated or only a partial delivery
- **THEN** the dispatch proceeds unchanged, and the run record records both the surfaced
  evidence and the operator's decision to continue

#### Scenario: No evidence produces no prompt
- **WHEN** the check reports `checked: true` with no matches, or reports `checked: false`
- **THEN** no operator prompt is shown and the dispatch proceeds without interruption

#### Scenario: A recognized deterministic predicate bypasses the operator prompt
- **WHEN** a claimed brief carries a `drift-source` predicate the system recognizes, and the
  predicate re-check for that brief completes without error
- **THEN** the operator is never prompted with probe-matched evidence for this brief; the
  predicate re-check's own outcome decides whether the brief is closed or the dispatch proceeds,
  and this requirement's evidence-surfacing and operator-decision behavior does not run for it

#### Scenario: A conclusive file-state verification bypasses the operator prompt
- **WHEN** the check reports one or more matching commits or pull requests for a claimed brief,
  the predicate re-check did not already determine the outcome, and the file-state verification
  step classifies the brief's requested capability as verifiably absent or verifiably present
- **THEN** the operator is never prompted with the probe-matched evidence for this brief; the
  verification outcome decides whether the dispatch proceeds or the brief is closed, and this
  requirement's evidence-surfacing and operator-decision behavior does not run for it

### Requirement: Deterministic Staleness Predicate Is Captured On Sweep-Generated Briefs

A brief filed by a sweep that can express its triggering condition as a deterministic,
machine-checkable predicate SHALL stamp that predicate's re-runnable inputs onto the brief as
structured frontmatter, keyed by the same `drift-source` marker value the sweep already writes.
For the checkbox-drift-sweep, the structured frontmatter SHALL be a `drift-findings` list with
one entry per captured hit, and each entry SHALL carry at minimum the hit's task-file `path`
relative to the repository root. This is in addition to, and SHALL NOT replace, the existing
human-readable prose rendering of the same findings in the brief body.

#### Scenario: A checkbox-drift brief carries structured, re-runnable findings
- **WHEN** the checkbox-drift-sweep files a brief for one or more `status: completed` task
  files with unchecked body checkboxes
- **THEN** the brief's frontmatter includes a `drift-findings` list with each affected task
  file's path, alongside the existing `drift-source: checkbox-drift-sweep` marker and the
  existing prose bullet list in the body

#### Scenario: Missing structured findings is treated as no predicate
- **WHEN** a brief carries `drift-source: checkbox-drift-sweep` but no `drift-findings`
  frontmatter (for example, a brief captured before this requirement existed)
- **THEN** a predicate re-check attempted against that brief cannot determine an outcome and the
  dispatch falls through to the unmodified probe-based staleness flow

### Requirement: Deterministic Predicate Re-Check Precedes Evidence Surfacing

For every brief-sourced dispatch, before the probe-based staleness search
(`check_brief_staleness`) runs or any evidence is surfaced to the operator, the system SHALL
check whether the claimed brief carries a `drift-source` value it recognizes and a non-empty
`drift-findings` list. When both are present, the system SHALL re-run that predicate's check
against the current base branch, restricted to the captured `drift-findings` entries, and SHALL
use the outcome of that re-check to decide the dispatch, bypassing the probe-based search and
operator prompt entirely. When either is absent, or the recognized predicate's re-check itself
fails (an unreadable or unparseable task file, or any other error), the system SHALL fall
through unchanged to today's probe-based `check_brief_staleness` search and operator-prompt
flow, exactly as if no predicate had been attempted.

#### Scenario: Recognized predicate with captured findings runs first
- **WHEN** a claimed brief carries `drift-source: checkbox-drift-sweep` and a non-empty
  `drift-findings` list
- **THEN** the predicate re-check runs before `check_brief_staleness` and before any operator
  prompt, and its outcome — not the probe-based search — decides the dispatch

#### Scenario: No drift-source falls through unchanged
- **WHEN** a claimed brief carries no `drift-source` frontmatter field
- **THEN** no predicate re-check is attempted and the dispatch proceeds through the unmodified
  probe-based `check_brief_staleness` search and operator-prompt flow

#### Scenario: Unrecognized drift-source falls through unchanged
- **WHEN** a claimed brief carries a `drift-source` value the system does not recognize
- **THEN** no predicate re-check is attempted for that value and the dispatch proceeds through
  the unmodified probe-based flow

#### Scenario: A re-check error falls through unchanged
- **WHEN** a recognized predicate's re-check raises or cannot read one of its captured
  `drift-findings` entries (for example, the task file no longer exists at the captured path)
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

### Requirement: Predicate Resolved Closes The Brief Automatically Citing The Re-Check

When a predicate re-check completes without error and finds that the condition which generated
the brief no longer holds for any captured finding, the system SHALL close the brief
automatically as already-delivered through the work-queue owner, before Phase 6 opens a run
record, citing the predicate re-check result itself as the reason for closure. The system SHALL
NOT cite matched commits or merged pull requests as the reason for this closure, because such
matches are not computed for a brief resolved this way and, even when coincidentally available,
are not proof that the predicate's own condition was what changed.

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

### Requirement: Checkbox-Drift Predicate Re-Check Reflects Current Task-File State

For `drift-source: checkbox-drift-sweep`, the predicate re-check SHALL determine each captured
finding's current state by reading that finding's task file directly from the current base
branch, not by reusing the counts captured when the brief was filed. A finding SHALL be
classified as still holding when its task file is currently `status: completed` with at least
one unchecked checkbox in the audited completion sections, and as resolved when the file is
currently `status: completed` with every audited checkbox checked, or is no longer `status:
completed` at all. A finding whose task file cannot be read (missing, moved, or unparseable)
SHALL cause the whole predicate re-check for that brief to be treated as an error, per the
Requirement: Deterministic Predicate Re-Check Precedes Evidence Surfacing.

#### Scenario: A finding's current checkbox state overrides its captured counts
- **WHEN** a captured finding recorded 3 unchecked checkboxes at capture time, and re-reading
  the task file now shows 0 unchecked checkboxes
- **THEN** the finding is classified as resolved, based on the current read, not the captured
  count

#### Scenario: A moved or deleted task file errors the whole re-check
- **WHEN** a captured finding's `path` no longer exists on the current base branch
- **THEN** the predicate re-check for that brief is treated as an error and falls through to
  the unmodified probe-based flow, rather than being classified as resolved

### Requirement: File-State Verification Precedes Evidence Surfacing

For every brief-sourced dispatch where the probe-based staleness search
(`check_brief_staleness`) reports one or more matching commits or pull requests, and the
deterministic predicate re-check did not already determine the outcome, the system SHALL
attempt to verify — by reading or grepping the brief's named paths and symbols — whether the
specific capability the brief's focus prose describes is currently present in, or absent from,
the matched files' current content. This verification SHALL classify the result as exactly one
of **verifiably absent**, **verifiably present**, or **inconclusive**, and SHALL run identically
regardless of whether the dispatch is interactive or `AUTO_MODE`, since it is an internal
reasoning step, not a human-facing prompt. When the classification is inconclusive, or the
verification step itself cannot be completed, the system SHALL fall through unchanged to
today's operator-prompt (interactive) or decision-record-plus-release (`AUTO_MODE`) flow,
exactly as if no verification had been attempted.

#### Scenario: Verification runs only when probe-based evidence exists
- **WHEN** the probe-based search reports `checked: true` with no matches and no pull requests
- **THEN** no file-state verification is attempted, because there is no evidence to verify
  against

#### Scenario: Verification is skipped when the predicate re-check already decided
- **WHEN** a claimed brief's deterministic predicate re-check already determined the dispatch
  outcome
- **THEN** the probe-based search and the file-state verification step do not run for that
  brief, consistent with Requirement: Deterministic Predicate Re-Check Precedes Evidence
  Surfacing

#### Scenario: Verification runs the same way in AUTO_MODE as interactively
- **WHEN** the probe-based search reports evidence for an `AUTO_MODE` dispatch
- **THEN** the file-state verification step is attempted before any decision-record is filed,
  exactly as it would be attempted before an interactive operator prompt

#### Scenario: An inconclusive classification falls through unchanged
- **WHEN** the file-state verification step cannot determine, from the matched files' current
  content, whether the brief's requested capability is present or absent
- **THEN** the classification is inconclusive, and the dispatch proceeds to today's unmodified
  operator-prompt or decision-record-plus-release flow, exactly as if no verification had run

#### Scenario: A verification failure falls through unchanged
- **WHEN** the file-state verification step cannot complete (for example, a named path no
  longer exists to read)
- **THEN** the outcome is treated as inconclusive, no auto-proceed or auto-close occurs on its
  basis, and the dispatch falls through to today's unmodified operator-prompt or
  decision-record-plus-release flow

### Requirement: Verified Absent Proceeds Automatically With Recorded Verification

When the file-state verification step classifies the brief's requested capability as verifiably
absent from current file state, the system SHALL proceed with the dispatch automatically,
without prompting the operator and without filing a decision record, and SHALL record both the
probe-based evidence and the verification finding on the run record once it is opened. The
system SHALL NOT omit this recording; a verifiably-absent outcome SHALL always be recorded,
never silently skipped.

#### Scenario: A verifiably-absent capability proceeds without a prompt
- **WHEN** the probe-based search surfaces a merged pull request touching a brief's named file,
  and the file-state verification step confirms the brief's specific requested capability is
  still absent from that file's current content
- **THEN** the dispatch proceeds to Phase 6/7 without an operator prompt and without a filed
  decision record, in both interactive and `AUTO_MODE` dispatches

#### Scenario: The verified-absent outcome is recorded on the run record
- **WHEN** the file-state verification outcome is verifiably absent and Phase 6 opens the run
  record for the proceeding dispatch
- **THEN** the run record carries an entry naming both the probe-based evidence (matched
  commits or pull requests) and the verification finding that the requested capability remains
  absent, and this entry is never omitted

### Requirement: Verified Present Closes The Brief Automatically Citing The Verification

When the file-state verification step classifies the brief's requested capability as verifiably
present in current file state, the system SHALL close the brief automatically as
already-delivered through the work-queue owner, before Phase 6 opens a run record, citing both
the probe-based evidence and the verification finding as the reason for closure.

#### Scenario: A verifiably-present capability closes the brief automatically
- **WHEN** the probe-based search surfaces a merged pull request touching a brief's named file,
  and the file-state verification step confirms the brief's specific requested capability is
  already present in that file's current content
- **THEN** the brief is closed as already-delivered automatically, with no operator prompt and
  no dispatch to Phase 6/7, in both interactive and `AUTO_MODE` dispatches

#### Scenario: The closure note cites both the probe evidence and the verification finding
- **WHEN** the system closes a brief under this requirement
- **THEN** the closure note names the probe-based evidence (matched commits or pull requests)
  and the verification finding that confirmed the requested capability's presence

