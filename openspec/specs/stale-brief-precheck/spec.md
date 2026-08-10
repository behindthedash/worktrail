# stale-brief-precheck Specification

## Purpose
TBD - created by archiving change stale-brief-precheck. Update Purpose after archive.
## Requirements
### Requirement: Evidence Probe Extraction From Brief Text

The system SHALL extract three kinds of Evidence Probe from a brief's focus text: **path
probes** (tokens shaped like a file path or a bare filename with an extension), **symbol
probes** (code-identifier-shaped tokens), and **pull-request probes** (explicit pull-request
references). Extraction SHALL be purely textual and SHALL NOT consult the repository.

Backtick-quoted tokens SHALL be preferred as probe sources. A token SHALL qualify as a path
probe when it contains a `/` separator **or** ends in a file extension of one to ten
characters; a bare filename with an extension therefore qualifies without needing a directory
component. An unquoted token SHALL additionally qualify as a **symbol** probe when it is
distinctively identifier-shaped — snake_case, with letters on both sides of an underscore —
because briefs captured through the primary capture path contain no backticks at all.

A token SHALL NOT qualify as a path probe when it is an absolute or home-relative path (it
names something outside the repository being searched), when it contains parentheses or angle
brackets (a prose call-site list or task chain, not a pathspec), or when its apparent path
structure carries no letters (a task id such as `1.1` or `2.1/2.2/2.3`, which is the single
most common token shape in a brief).

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

#### Scenario: Ordinary unquoted prose is not a symbol probe
- **WHEN** a brief's focus text contains ordinary words and hyphenated words such as
  "resolve the base ref" and "file-scope"
- **THEN** none of them are extracted as symbol probes

#### Scenario: Task ids and absolute paths are not path probes
- **WHEN** a brief's focus text contains `1.1`, `2.1/2.2/2.3/2.4`, and a `repo:` line naming an
  absolute path
- **THEN** none of them are extracted as path probes

#### Scenario: Pull-request references are extracted with their number
- **WHEN** a brief's focus text contains `devops PR #89` and `behindthedash/devops#89`
- **THEN** pull-request probe `89` is extracted, deduplicated to a single entry

#### Scenario: Prose without code-shaped tokens yields no probes
- **WHEN** a brief's focus text is ordinary prose containing no backtick-quoted tokens, no
  path-shaped tokens, and no pull-request references
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
restricted to commits authored at or after the brief's `created:` timestamp. Path probes SHALL
be searched by path; symbol probes SHALL be searched **both** by change-in-occurrence-count
(`git log -S`) and by commit message (`git log --grep`), since a commit that moved, reverted, or
merely described the work can name the symbol in its subject without changing its occurrence
count. Each reported match SHALL carry the kind of search that found it, and a commit found by
more than one search for the same probe SHALL be reported once. The base branch SHALL be
resolved preferring the remote-tracking ref when one exists, so the search sees work that landed
upstream but has not been merged into the local checkout.

#### Scenario: A commit landing after capture is reported as evidence
- **WHEN** a brief was captured on 2026-07-31 and a commit touching one of its path probes
  landed on the base branch on 2026-08-02
- **THEN** that commit is reported as a match, carrying its short SHA, commit date, and subject

#### Scenario: A commit predating capture is not evidence
- **WHEN** the only commit touching a probe landed before the brief's `created:` timestamp
- **THEN** it is not reported as a match, because work predating capture cannot be what the
  brief was filed against

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
that CLI as "no signal" rather than as an error or as absence of evidence.

#### Scenario: GitHub CLI unavailable degrades without failing
- **WHEN** `gh` is not installed, not authenticated, or times out
- **THEN** the pull-request section of the result is empty, a warning names the cause, and any
  evidence already found by the git history search is still reported

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
the brief or continuing the dispatch. The system SHALL NOT close, stamp, move, or otherwise
mutate the brief on the basis of this check alone. Brief lifecycle mutations SHALL continue to
be performed only through the work-queue owner, and only as the result of an explicit operator
choice.

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

