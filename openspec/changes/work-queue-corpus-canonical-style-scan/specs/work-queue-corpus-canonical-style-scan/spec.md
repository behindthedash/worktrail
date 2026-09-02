## Purpose

Defines the read-only corpus-wide scan that detects work-queue briefs whose frontmatter was not
written through `serialize_frontmatter()`, so drift from writers this repo does not control (or a
future internal writer that skips the canonical serializer) is diagnosable instead of invisible.

## ADDED Requirements

### Requirement: Corpus scan detects non-canonical frontmatter style
Scanning a work-queue root SHALL evaluate every `.md` file under its `queue/` and `picked/`
directories and report each file whose frontmatter block does not match what
`serialize_frontmatter()` would produce for the same parsed values. A file whose frontmatter block
round-trips through canonical serialization unchanged SHALL NOT be reported.

#### Scenario: Non-canonically styled brief is reported
- **WHEN** a brief under `queue/` or `picked/` has a frontmatter block that parses correctly but
  was not written in `serialize_frontmatter()`'s scalar style (e.g. a `focus:` value in plain or
  folded style instead of the canonical `|-` literal block)
- **THEN** the scan reports that file

#### Scenario: Canonically styled brief produces no finding
- **WHEN** a brief's frontmatter block matches what `serialize_frontmatter()` would produce for its
  parsed values
- **THEN** the scan does not report that file

#### Scenario: Scan covers both queue/ and picked/
- **WHEN** the work-queue root has non-canonical briefs in both `queue/` and `picked/`
- **THEN** the scan reports files from both directories

### Requirement: Scan distinguishes style mismatches from malformed frontmatter
Each reported file SHALL be classified as either `style-mismatch` (frontmatter parses to a
non-empty mapping but does not match canonical serialization) or `malformed` (no `---`-fenced
frontmatter block is found, or the block does not parse to a non-empty mapping). These two
classifications SHALL NOT be merged into a single undifferentiated finding.

#### Scenario: Brief missing a frontmatter block is classified malformed
- **WHEN** a `.md` file under `queue/` or `picked/` has no `---`-fenced frontmatter block
- **THEN** the scan reports it with classification `malformed`, not `style-mismatch`

#### Scenario: Brief with unparseable YAML frontmatter is classified malformed
- **WHEN** a brief's frontmatter block is present but fails to parse as YAML, or parses to
  something other than a mapping
- **THEN** the scan reports it with classification `malformed`

#### Scenario: Brief with parseable but wrongly styled frontmatter is classified style-mismatch
- **WHEN** a brief's frontmatter block parses to a non-empty mapping but its on-disk text differs
  from canonical serialization of that mapping
- **THEN** the scan reports it with classification `style-mismatch`

### Requirement: Scan is read-only
Scanning the work-queue corpus SHALL NOT modify, move, rename, or otherwise alter any file's bytes
or filesystem metadata, regardless of what it finds.

#### Scenario: Corpus is unchanged after a scan that finds violations
- **WHEN** a scan runs over a work-queue root containing malformed and style-mismatched briefs
- **THEN** every file's bytes and mtime are identical before and after the scan

### Requirement: Scan output supports external scheduling
The scan SHALL be invocable as a standalone command that accepts a work-queue root (defaulting to
`$WORK_QUEUE_DIR`), supports a JSON output mode listing every finding with its file path and
classification, and exits with a nonzero status when any finding exists and zero when the corpus is
clean, so it can be wired into a cron job, launchd task, or external CI pipeline without this repo
owning that schedule.

#### Scenario: JSON output lists every finding
- **WHEN** the scan is invoked in JSON mode over a work-queue root with violations
- **THEN** the output is machine-readable and includes, for each finding, its file path and
  whether it is `style-mismatch` or `malformed`

#### Scenario: Exit code signals a dirty corpus
- **WHEN** the scan finds at least one non-canonical or malformed file
- **THEN** the command exits with a nonzero status

#### Scenario: Exit code signals a clean corpus
- **WHEN** the scan finds no non-canonical or malformed files
- **THEN** the command exits with status zero
