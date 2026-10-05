## ADDED Requirements

### Requirement: A keep verdict may record an external blocker

A `keep` verdict SHALL accept an optional `blocked_on` field: a non-empty single-line
description of a blocker that is not a queue-brief prerequisite -- a dependency in another
repository, an upstream pull request or branch that must land, or an out-of-band operator
action -- and that therefore cannot be expressed as a `blocked-by` reference (which lists
queue-brief IDs only) or a `next-check-after` date (which requires a known date). The
`evaluate` step SHALL treat the field as optional and SHALL NOT downgrade a `keep` verdict, or
alter its recorded evidence, for carrying, omitting, or malforming it; a `blocked_on` supplied
on any verdict other than `keep` SHALL be ignored.

The evaluator prompt SHALL instruct the evaluator to set `blocked_on` only when the brief is
still valid but externally blocked, and to fail open toward *remaining* blocked: the prompt
SHALL show the brief's current `blocked-on:` value, instruct the evaluator to echo that value
in the `keep` verdict when the blocker is still unresolved or the evaluator cannot verify that
it has cleared, and permit an empty string for the field only when the evaluator has evidence
the blocker has cleared.

When a `keep` verdict is applied with `--confirm`, the brief's `blocked-on:` frontmatter SHALL
be reconciled to the verdict: a non-empty value SHALL be written, replacing any existing value
(and quoted as a YAML scalar when the value requires it); an explicitly empty value SHALL
remove the field; and an absent field SHALL leave the brief's existing `blocked-on:` value
unchanged. The reconciliation SHALL NOT alter any other frontmatter. A `work-directly` verdict
that is accepted and stamps the brief's `seeded-from` SHALL remove any `blocked-on:` field,
because a brief being seeded for direct execution is no longer blocked. When a `keep` is
previewed (no `--confirm`), the preview entry SHALL report the `blocked_on` value the verdict
carried, so a preview never omits a pending change to the brief's blocker.

#### Scenario: A keep records a new external blocker

- **WHEN** an evaluator returns `keep` for a valid brief that is blocked on an upstream pull
  request in another repository, with `blocked_on` naming that pull request, and the keep is
  applied with `--confirm`
- **THEN** the brief gains the `## Triage <run-date>` note exactly as before this requirement
  and its frontmatter gains `blocked-on:` set to that text

#### Scenario: An unverifiable blocker is preserved

- **WHEN** a brief already carries `blocked-on: devops PR #42 must land` and the evaluator
  returns `keep` with that same value echoed (it could not verify the pull request had merged)
- **THEN** the brief's `blocked-on:` is unchanged

#### Scenario: A keep with no blocked_on leaves the field alone

- **WHEN** a brief carries `blocked-on:` and the evaluator returns `keep` without the field
- **THEN** the brief's `blocked-on:` is unchanged -- an absent field never clears an existing
  blocker

#### Scenario: A keep with an empty blocked_on clears the blocker

- **WHEN** a brief carries `blocked-on:` and the evaluator returns `keep` with `blocked_on` set
  to the empty string, citing evidence the blocker has cleared
- **THEN** the applied brief no longer has a `blocked-on:` field and is eligible for
  automatic selection again

#### Scenario: blocked_on on a non-keep verdict is ignored

- **WHEN** an evaluator returns `stale-close` (or any verdict other than `keep`) carrying a
  `blocked_on` value
- **THEN** the brief's `blocked-on:` field is not written from it

#### Scenario: work-directly clears an external blocker when it seeds

- **WHEN** a brief carries `blocked-on:` and the evaluator returns an accepted `work-directly`
  verdict that stamps `seeded-from:`
- **THEN** the brief no longer carries a `blocked-on:` field, so the seeded brief is not held
  out of automatic selection by a stale blocker

#### Scenario: Prompt shows the current blocker and the preserve rule

- **WHEN** `_evaluate_group()` formats the evaluator prompt for a brief carrying
  `blocked-on: <reason>`
- **THEN** the formatted prompt shows that current value and instructs the evaluator to echo
  it when the blocker is unresolved and to use an empty value only with evidence it has
  cleared

#### Scenario: Preview reports the pending blocker

- **WHEN** the apply step previews (no `--confirm`) a `keep` verdict carrying a `blocked_on`
  value
- **THEN** the preview entry reports that value, and the brief is unchanged
