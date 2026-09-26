## MODIFIED Requirements

### Requirement: Content-only comparison for workflow and script templates
For files with no such operator-growth field (each recognized auto-merge workflow, the
openspec-validate workflow, the rulesets-drift-guard workflow, and its vendored
`rulesets_sync.py`/`requirements.txt`), drift detection SHALL compare the full file content
against the current generator's output. The recognized auto-merge paths are
`.github/workflows/worktrail-auto-merge.yml` and `.github/workflows/auto-merge.yml`; when both
exist, each SHALL be compared and reported independently.

#### Scenario: Stale workflow content flagged
- **WHEN** an existing scaffolded workflow or vendored script file's content does not
  byte-for-byte match what its generator function produces today
- **THEN** that file is reported as drifted

#### Scenario: Legacy auto-merge workflow content flagged
- **WHEN** `.github/workflows/auto-merge.yml` exists and differs byte-for-byte from
  `build_automerge_workflow()`
- **THEN** that legacy path is reported as drifted
