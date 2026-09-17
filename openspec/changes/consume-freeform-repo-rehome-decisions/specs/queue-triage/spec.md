## ADDED Requirements

### Requirement: Free-form repo re-home decisions are consumed
During inventory, `evaluate` SHALL consider every brief that links an answered decision,
whether or not the brief already has a `repo:` value. When the decision's question is not
the canonical repo-assignment question, the decision SHALL be consumed only if its answer
contains an explicit re-home directive — a re-home, move, retarget, or reassign verb followed
by "to" and a repo name, optionally preceded by "the" and followed by "repo" — and that name
resolves to an on-disk checkout under the repos root. Consuming SHALL write the checkout path
as the brief's `repo:` (replacing any existing value), append a `verdict: repo-inferred`
triage note with rule `decision`, archive the decision, and group the brief under the new
repo in the same run. When the answer has no such directive, or the named repo does not
resolve, the brief and the decision SHALL be left untouched and SHALL NOT be reported as an
unresolvable repo assignment.

#### Scenario: Free-form re-home answer is consumed
- **WHEN** a brief with `repo: /repos/worktrail` links an answered decision whose question
  is "Does this belong in worktrail?" and whose answer is "Re-home the brief to the devops
  repo group", and a `devops` checkout exists under the repos root
- **THEN** the brief's `repo:` becomes the `devops` checkout path, a `verdict: repo-inferred`
  note with rule `decision` is appended, the decision is archived, and the brief is grouped
  under `devops` in that run

#### Scenario: Free-form answer without a directive is ignored
- **WHEN** a brief links an answered decision whose question is "Should we keep the retry?"
  and whose answer is "Yes, keep it"
- **THEN** the brief and decision are unchanged and no unresolvable entry is reported

#### Scenario: Directive naming an unknown repo is ignored
- **WHEN** the answer is "Move it to the nonesuch repo" and no `nonesuch` checkout exists
- **THEN** the brief and decision are unchanged and no unresolvable entry is reported
