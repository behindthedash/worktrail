# openspec-spec-purpose-hygiene Specification

## Purpose
Requires every canonical capability spec to say what the capability is for, and fails a PR
that would merge one still carrying the `TBD` stub `openspec archive` writes. Prevents the
only prose in a spec from naming the change that archived it instead of the capability
itself, which makes an undocumented capability indistinguishable from a documented one to
anyone -- or anything -- orienting on it. Scoped to the specs changed in the diff, so the
gate ratchets forward rather than blocking every PR on the whole tree's backlog.
## Requirements
### Requirement: A changed capability spec SHALL state a real Purpose
`check_spec_purpose.check_changed_specs(repo, changed_paths)` SHALL return one failure message
per changed `openspec/specs/<capability>/spec.md` whose `## Purpose` section is absent, empty,
or whose first non-blank body line begins with `TBD` (case-insensitive) — the stub
`openspec archive` writes. A spec whose Purpose carries any other prose SHALL pass: this is a
placeholder check, not a quality review, and it SHALL NOT inspect wording, length, or style.

#### Scenario: the archive placeholder is rejected
- **WHEN** a changed spec's Purpose reads `TBD - created by archiving change foo. Update Purpose after archive.`
- **THEN** a failure naming that spec's capability is returned

#### Scenario: a missing or empty Purpose section is rejected
- **WHEN** a changed spec has no `## Purpose` heading, or has one whose body is blank before
  the next heading
- **THEN** a failure naming that spec's capability is returned

#### Scenario: real prose passes
- **WHEN** a changed spec's Purpose describes what the capability does
- **THEN** no failure is returned for it

### Requirement: The check SHALL be scoped to the diff, never the whole tree
The check SHALL consider only paths supplied in `changed_paths` that match
`openspec/specs/<capability>/spec.md`, and SHALL ignore every other path, including delta specs
under `openspec/changes/**` and devkit-format specs under `docs/specs/**`. A repo with no
changed capability spec SHALL produce no failures, regardless of what the rest of
`openspec/specs/` contains.

#### Scenario: an unchanged placeholder spec does not fail a PR
- **WHEN** `openspec/specs/other-cap/spec.md` still carries a placeholder Purpose but is not in
  `changed_paths`
- **THEN** no failure is returned for it

#### Scenario: a delta spec under a change is not checked
- **WHEN** `changed_paths` contains `openspec/changes/some-change/specs/cap/spec.md`
- **THEN** no failure is returned for it

### Requirement: The pre-PR gate SHALL fail on Purpose drift with its own exit code
`pre_pr_gate.run_drift_checks` SHALL run the Purpose check after the req/AC coverage check, and
on failure SHALL print each failure message to stderr under a `PRE-PR GATE: FAIL` heading and
return exit code `6`, distinct from every existing drift exit code. `--checks-only` SHALL
include this check alongside the existing deterministic checks.

#### Scenario: a placeholder Purpose blocks the gate
- **WHEN** the diff changes a capability spec whose Purpose is still the archive placeholder
- **THEN** `run_drift_checks` returns `6` and stderr names the offending capability

#### Scenario: passing specs leave the gate verdict unchanged
- **WHEN** every changed capability spec states a real Purpose
- **THEN** `run_drift_checks` returns whatever the pre-existing checks return, unchanged

### Requirement: Existing placeholder Purposes SHALL be backfilled
Every `openspec/specs/<capability>/spec.md` in this repo that carries the
`TBD - created by archiving change` placeholder SHALL be replaced with a Purpose written from
that spec's own requirements, stating what the capability does and why it exists. No requirement
text, scenario, or heading outside the Purpose section SHALL be modified.

#### Scenario: no placeholder remains
- **WHEN** `grep -rl "TBD - created by archiving change" openspec/specs/` is run after the
  backfill
- **THEN** it matches no file

#### Scenario: requirements are untouched
- **WHEN** the backfill diff is reviewed
- **THEN** every hunk falls inside a `## Purpose` section

