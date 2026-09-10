# close-stale-archive-delta-precheck Specification

## Purpose
TBD - created by archiving change close-stale-archive-delta-precheck. Update Purpose after archive.
## Requirements
### Requirement: Close-stale runs a delta pre-check before mutating the worktree

`flip_and_archive` SHALL, after locating the change's `tasks.md` and before flipping any
checkbox, run `openspec validate <change_id> --strict` in the worktree and structurally
compare every delta file under `openspec/changes/<change_id>/specs/**/spec.md` against the
canonical `openspec/specs/` tree. When the pre-check refuses, the function SHALL return with
`archived` false, `flipped` empty, an `error` naming the refusal class and the first
offending capability/requirement, and SHALL NOT have modified `tasks.md` or run
`openspec archive`. The result SHALL always carry a `precheck` object with
`validate_ok`, `validate_output`, `missing_canonical`, `delta_drift`, and `drift_allowed`
fields whenever `checked` is true.

#### Scenario: openspec validate fails

- **WHEN** `openspec validate <change_id> --strict` exits non-zero
- **THEN** the result has `error` set, `precheck.validate_ok` false with the command's output
  in `precheck.validate_output`, `flipped` empty, `archived` false, and `tasks.md` is unchanged

#### Scenario: A MODIFIED, REMOVED, or RENAMED-FROM requirement is absent from the canonical spec

- **WHEN** a delta file declares a `MODIFIED` or `REMOVED` requirement heading, or a
  `RENAMED ... FROM:` name, that is not a `### Requirement:` heading in
  `openspec/specs/<capability>/spec.md`, or that canonical file does not exist
- **THEN** the result has `error` set, `precheck.missing_canonical` lists each such
  `{capability, requirement, kind}`, `flipped` is empty, `archived` is false, and
  `openspec archive` is not run

#### Scenario: An ADDED requirement has no canonical file yet

- **WHEN** a delta file declares only `ADDED` requirements under a capability with no
  `openspec/specs/<capability>/spec.md`
- **THEN** `precheck.missing_canonical` is empty for that capability and the pre-check does not
  refuse on its account

#### Scenario: Pre-check passes

- **WHEN** validate succeeds, `missing_canonical` is empty, and `delta_drift` is empty
- **THEN** checkboxes are flipped and `openspec archive -y <change_id> --json` runs exactly as
  before, and `precheck.validate_ok` is true

### Requirement: Close-stale refuses on archived-sibling delta drift unless explicitly allowed

The pre-check SHALL reuse the dashboard's `_openspec_delta_drift` check (as specified by
`openspec-delta-drift-detection`) against the change directory and worktree. When it reports
one or more findings and drift is not allowed, the function SHALL refuse with the findings in
`precheck.delta_drift` and no mutation. The CLI SHALL accept `--allow-delta-drift`, which
bypasses this class only; validate failure and missing canonical targets SHALL remain
non-overridable. The result SHALL record the override as `precheck.drift_allowed`.

#### Scenario: An archived sibling overtook the change's delta

- **WHEN** the drift check reports a requirement under a capability whose archived sibling
  postdates the change's delta, and `--allow-delta-drift` is not passed
- **THEN** the result has `error` set naming the requirement and archived change id,
  `precheck.delta_drift` lists the finding, `flipped` is empty, and `archived` is false

#### Scenario: Drift is explicitly allowed

- **WHEN** the same drift is reported and `--allow-delta-drift` is passed
- **THEN** `precheck.delta_drift` still lists the finding, `precheck.drift_allowed` is true,
  and the flip-and-archive proceeds

#### Scenario: Override does not bypass the other classes

- **WHEN** `--allow-delta-drift` is passed and either validate fails or `missing_canonical` is
  non-empty
- **THEN** the pre-check still refuses with no mutation

### Requirement: The worktrail-go close-stale dispatch row documents the pre-check

The `close-stale` row of `skills/worktrail-go/SKILL.md`'s action dispatch table SHALL state
that `worktrail-close-stale-openspec` runs `openspec validate --strict` and a delta-vs-canonical
pre-check before flipping any checkbox, that a refusal leaves the worktree unmodified and
reports the offending requirement in the JSON `precheck` field, and that
`--allow-delta-drift` exists for the archived-sibling drift class only.

#### Scenario: Skill text names the pre-check and the override

- **WHEN** `tests/test_plugin_surface.py` inspects the close-stale row
- **THEN** it finds `openspec validate` and `--allow-delta-drift` named in that row

