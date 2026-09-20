# repo-init-policy-seed Specification

## Purpose
Seeds a scaffolded `.worktrail/policy.yaml` with the fleet's auto-merge default (`enabled: true`,
`max_risk: medium`) instead of commented-out placeholders. Prevents a newly onboarded repo from
shipping an auto-merge workflow that is silently inert.
## Requirements
### Requirement: Seed the fleet's auto-merge default into a scaffolded policy
`default_policy_yaml()` SHALL emit an `automerge` block with `enabled: true` and
`max_risk: medium` — the fleet-wide default — rather than header comments alone, so a
newly onboarded repo's scaffolded auto-merge workflow is live rather than inert. The
emitted YAML SHALL parse to exactly that mapping, and
`skills/worktrail-repo-init/SKILL.md` SHALL document the seeded default rather than
instructing the operator not to guess automerge settings.

#### Scenario: A scaffolded policy arms auto-merge at the documented ceiling
- **WHEN** `default_policy_yaml("some-repo")` is parsed as YAML
- **THEN** it contains `automerge: {enabled: true, max_risk: medium}`

#### Scenario: The seed composes with the other optional keys
- **WHEN** `default_policy_yaml("some-repo", enable_aspens=True, pre_commit_cmd="ruff format .")`
  is parsed as YAML
- **THEN** it contains the `automerge` mapping above, the `add_ons.aspens` key, and
  `pre_commit_cmd`

#### Scenario: An existing policy file is never rewritten
- **WHEN** `propose` runs against a repo that already has `.worktrail/policy.yaml`
- **THEN** the file is reported as skipped and left byte-identical, as today

