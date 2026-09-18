## MODIFIED Requirements

### Requirement: Free-form repo re-home decisions are consumed
During inventory, `evaluate` SHALL consider every brief that links an answered decision,
whether or not the brief already has a `repo:` value. When the decision's question is not
the canonical repo-assignment question, the decision SHALL be consumed as a re-home only if
its answer contains an explicit re-home directive — a re-home, move, retarget, reassign, or
re-scope verb (hyphen optional in "re-home" and "re-scope", matched case-insensitively)
followed by "to" and a repo name, optionally preceded by "the" and followed by "repo" — and
that name resolves to an on-disk checkout under the repos root. Consuming a re-home SHALL
write the checkout path as the brief's `repo:` (replacing any existing value), append a
`verdict: repo-inferred` triage note with rule `decision`, archive the decision, and group
the brief under the new repo in the same run. When the answer contains such a directive but
the named repo does not resolve, the brief and the decision SHALL be left untouched and SHALL
NOT be reported as an unresolvable repo assignment. When the answer has no such directive,
the decision SHALL instead be consumed as answered guidance (see "Answered guidance
decisions are consumed"), and SHALL NOT be reported as an unresolvable repo assignment.

#### Scenario: Free-form re-home answer is consumed
- **WHEN** a brief with `repo: /repos/worktrail` links an answered decision whose question
  is "Does this belong in worktrail?" and whose answer is "Re-home the brief to the devops
  repo group", and a `devops` checkout exists under the repos root
- **THEN** the brief's `repo:` becomes the `devops` checkout path, a `verdict: repo-inferred`
  note with rule `decision` is appended, the decision is archived, and the brief is grouped
  under `devops` in that run

#### Scenario: Re-scope answer is consumed
- **WHEN** a brief with `repo: /repos/devops` links an answered decision whose question is
  not the canonical repo-assignment question and whose answer is "Re-scope the brief's repo
  to worktrail", and a `worktrail` checkout exists under the repos root
- **THEN** the brief's `repo:` becomes the `worktrail` checkout path, a
  `verdict: repo-inferred` note with rule `decision` is appended, the decision is archived,
  and the brief is grouped under `worktrail` in that run

#### Scenario: Unhyphenated rescope answer is consumed
- **WHEN** the answer is "rescope it to the worktrail repo" and a `worktrail` checkout exists
- **THEN** the decision is consumed exactly as for the hyphenated form

#### Scenario: Free-form answer without a directive is ignored
- **WHEN** a brief links an answered decision whose question is "Should we keep the retry?"
  and whose answer is "Yes, keep it"
- **THEN** the brief's `repo:` and group are unchanged (the re-home path ignores it), no
  unresolvable entry is reported,
  and the decision is consumed as answered guidance

#### Scenario: Directive naming an unknown repo is ignored
- **WHEN** the answer is "Move it to the nonesuch repo" and no `nonesuch` checkout exists
- **THEN** the brief and decision are unchanged and no unresolvable entry is reported

## ADDED Requirements

### Requirement: Answered guidance decisions are consumed
During inventory, when a brief links an answered decision that is neither the canonical
repo-assignment question nor a free-form answer carrying a re-home directive, `evaluate`
SHALL consume it as answered guidance: it SHALL append a `verdict: decision-answered` triage
note carrying the decision id, the question, and the answer (whitespace collapsed to a single
line), archive the decision (clearing the brief's `awaiting-decision` link), leave `repo:`
and the brief's group unchanged, and include the brief in that run's evaluation set. A
`decision-answered` note SHALL NOT count toward the recent-triage dedup window. The brief
SHALL NOT be reported as an inferred repo nor as an unresolvable repo assignment.

The evaluator prompt line for a brief carrying one or more `decision-answered` notes SHALL
include the most recent note's question and answer as a human decision, and the prompt SHALL
instruct the evaluator to treat it as settled: a `needs-decision` verdict re-asking that
question is not an acceptable outcome.

#### Scenario: Keep-under-repo answer unblocks the brief
- **WHEN** a brief with `repo: /repos/worktrail` links an answered decision whose question is
  "Does this brief belong in worktrail or devops?" and whose answer is "keep under worktrail,
  contract change"
- **THEN** a `verdict: decision-answered` note naming that decision id, question, and answer
  is appended, the decision is archived and the brief's `awaiting-decision` link is cleared,
  `repo:` is still `/repos/worktrail`, and the brief appears in the `/repos/worktrail`
  evaluation group of the same run rather than being held

#### Scenario: Answered guidance does not trigger the dedup skip
- **WHEN** a brief's only triage note is a `decision-answered` note written today
- **THEN** the brief is not treated as recently triaged and is not added to the skipped list

#### Scenario: Evaluator prompt carries the answer
- **WHEN** the evaluator prompt is built for a group containing a brief whose most recent
  `decision-answered` note has question "Should we keep the retry?" and answer "Yes, keep it"
- **THEN** that brief's line in the prompt contains both the question and the answer, and the
  prompt text states that re-asking an answered question via `needs-decision` is not allowed

#### Scenario: Open decision is still held
- **WHEN** a brief links a decision that is still `open`
- **THEN** nothing is consumed, no note is written, and the brief is excluded from the
  evaluation set as today

#### Scenario: Unresolvable canonical answer is still reported, not consumed as guidance
- **WHEN** a repo-less brief links an answered canonical repo-assignment decision whose answer
  is "the nonesuch repo" and no such checkout exists
- **THEN** the brief and decision are unchanged, no `decision-answered` note is written, and
  the brief is reported as an unresolvable repo assignment
