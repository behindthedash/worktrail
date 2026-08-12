## MODIFIED Requirements

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

#### Scenario: Pull-request references are extracted with their number
- **WHEN** a brief's focus text contains `devops PR #89` and `behindthedash/devops#89`
- **THEN** pull-request probe `89` is extracted, deduplicated to a single entry

#### Scenario: Prose without code-shaped tokens yields no probes
- **WHEN** a brief's focus text is ordinary prose containing no backtick-quoted tokens, no
  path-shaped tokens, no CLI-flag-shaped tokens, and no pull-request references
- **THEN** all three probe lists are empty and the check reports no evidence

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
