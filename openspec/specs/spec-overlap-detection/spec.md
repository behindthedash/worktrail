# spec-overlap-detection Specification

## Purpose
TBD - created by archiving change overlap-check-openspec-format-support. Update Purpose after archive.
## Requirements
### Requirement: Devkit-Shaped Root Scanning Is Unchanged
The system SHALL scan a devkit-shaped root (a directory whose immediate
children match `^\d{3,}-`) exactly as before this change: each matching
child directory is read for a spec file / `user-request.md`, with the
existing `**Feature Summary**` → `### Problem Statement` → `user-request.md`
extraction priority.

#### Scenario: Existing devkit root produces identical output
- **WHEN** `scan()` is called with a root containing `001-donor-search/`
  (an `NNN-slug` directory with a spec file)
- **THEN** the returned entry for `001-donor-search` has the same
  `spec_id`, `stage`, `title`, `feature_summary`, and `user_request_excerpt`
  values as before this change

### Requirement: OpenSpec-Shaped Root Detection
The system SHALL treat a root as OpenSpec-shaped when it contains a
`changes/` subdirectory, a `specs/` subdirectory, or both, and SHALL scan it
using the OpenSpec extraction rules below instead of the devkit
`^\d{3,}-`-prefix iteration.

#### Scenario: Root with only a changes/ subdirectory is OpenSpec-shaped
- **WHEN** `scan()` is called with a root containing `changes/some-change/proposal.md`
  and no `specs/` subdirectory
- **THEN** the root is scanned via OpenSpec extraction, not devkit extraction

#### Scenario: Root with only a specs/ subdirectory is OpenSpec-shaped
- **WHEN** `scan()` is called with a root containing `specs/some-capability/spec.md`
  and no `changes/` subdirectory
- **THEN** the root is scanned via OpenSpec extraction, not devkit extraction

#### Scenario: Root matching neither shape returns an empty list
- **WHEN** `scan()` is called with a root that has no `changes/` or `specs/`
  subdirectory and no `NNN-slug` children
- **THEN** the returned `specs` array is empty, unchanged from current
  behavior for an empty or unrecognized root

### Requirement: OpenSpec Change Feature-Summary Extraction
For each directory under an OpenSpec root's `changes/` containing a
`proposal.md`, the system SHALL extract a feature summary using this
priority: the `## Capabilities` section's body text, falling back to the
first sentence of the `## Why` section if `## Capabilities` is absent or
empty.

#### Scenario: Capabilities section is preferred
- **WHEN** a `changes/add-x/proposal.md` has both a non-empty `## Why`
  section and a non-empty `## Capabilities` section
- **THEN** the extracted `feature_summary` comes from `## Capabilities`

#### Scenario: Falls back to Why when Capabilities is empty
- **WHEN** a `changes/add-x/proposal.md` has a non-empty `## Why` section
  and an empty (template-only) `## Capabilities` section
- **THEN** the extracted `feature_summary` is the first sentence of `## Why`

#### Scenario: No matching section produces a null summary, not a crash
- **WHEN** a `changes/add-x/proposal.md` has neither a `## Capabilities`
  nor a `## Why` section with content
- **THEN** the entry for `add-x` is still returned with `feature_summary: null`

### Requirement: OpenSpec Capability Feature-Summary Extraction
For each directory under an OpenSpec root's `specs/` containing a
`spec.md`, the system SHALL extract a feature summary from that file's
`## Purpose` section.

#### Scenario: Purpose section is extracted
- **WHEN** a `specs/duplicate-brief-detection/spec.md` has a non-empty
  `## Purpose` section
- **THEN** the extracted `feature_summary` is that section's text

### Requirement: OpenSpec Entry Stage Reporting
The system SHALL report `stage: "active"` for every entry sourced from
`changes/` and `stage: "complete"` for every entry sourced from `specs/`.

#### Scenario: In-flight change reports active
- **WHEN** an entry is extracted from `changes/add-x/proposal.md`
- **THEN** its `stage` field is `"active"`

#### Scenario: Archived capability reports complete
- **WHEN** an entry is extracted from `specs/duplicate-brief-detection/spec.md`
- **THEN** its `stage` field is `"complete"`

### Requirement: Combined Output Shape Is Format-Agnostic
Every entry returned by `scan()`, regardless of source format, SHALL have
the same `{spec_id, stage, title, feature_summary, user_request_excerpt}`
keys, so a caller comparing entries from a devkit root and an OpenSpec root
(from two separate `scan()` calls, merged) does not need format-specific
handling.

#### Scenario: OpenSpec entry has the same keys as a devkit entry
- **WHEN** one entry comes from a devkit root and another from an OpenSpec
  root
- **THEN** both entries are dicts with exactly the same key set (an OpenSpec
  entry's `user_request_excerpt` is `null`, since OpenSpec has no
  `user-request.md` equivalent)

### Requirement: OpenSpec Per-Task Candidate Enumeration
When a caller supplies an OpenSpec change id as an explicit target, the system SHALL be able to
return one candidate per unchecked task in that change's `tasks.md`, each with the same
`{task_id, task_text, checked}` shape, in addition to the existing whole-change candidate. This
mode SHALL only run for the supplied target change; it SHALL NOT be invoked as a scan across
every change under an OpenSpec root.

#### Scenario: Target change with unchecked tasks
- **WHEN** a caller requests per-task candidates for an OpenSpec change id whose `tasks.md`
  contains unchecked task lines
- **THEN** one candidate is returned per unchecked task, each carrying its task id, task line
  text, and `checked: false`

#### Scenario: Target change fully checked
- **WHEN** a caller requests per-task candidates for an OpenSpec change id whose `tasks.md` has
  no unchecked tasks
- **THEN** an empty per-task candidate list is returned, with no error

#### Scenario: No target supplied
- **WHEN** a caller performs the existing whole-root `scan()` with no target change id
- **THEN** behavior is unchanged from before this requirement — whole-change candidates only, no
  per-task entries

