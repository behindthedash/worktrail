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
