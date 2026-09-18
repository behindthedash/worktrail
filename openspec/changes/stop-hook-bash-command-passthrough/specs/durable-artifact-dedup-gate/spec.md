## MODIFIED Requirements

### Requirement: Merged Docs-Only Spec PR Detection Is Transcript-Local

The Stop hook SHALL collect the raw Bash command text of every Bash tool call during its
existing single transcript pass, alongside the run-record and touched-durable-artifact
signals it already collects, and SHALL forward each collected command to the dedup check via
`--bash-command`. The dedup check SHALL report a merged docs-only spec PR hit when the
forwarded evidence shows both a PR merge marker (`gh pr merge`, merged-PR reference) AND
session-touched paths under `docs/specs/**` or `openspec/changes/**`. The check SHALL NOT make
network calls to GitHub.

#### Scenario: In-session spec merge detected

- **WHEN** the transcript contains a `gh pr merge` command and Edit calls under
  `docs/specs/<slug>/`
- **THEN** a merged docs-only spec PR hit is reported

#### Scenario: No network at hook time

- **WHEN** the Stop hook runs offline
- **THEN** the merged-docs-only-spec-PR detection still evaluates from transcript content alone

#### Scenario: Hook forwards collected Bash commands to the checker

- **WHEN** the Stop hook's transcript scan collects one or more Bash command texts
- **THEN** the hook passes each one to `worktrail-check-durable-artifact-capture-gate` as a
  separate `--bash-command` argument, so the checker's merge-marker detection sees real
  command text rather than an empty list
