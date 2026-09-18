## ADDED Requirements

### Requirement: Proceed-as-scoped answer confirms a repo-less brief
When a brief with no `repo:` value links an answered decision whose question is the canonical
repo-assignment question and whose answer is the offered option "Proceed with the brief as
currently scoped" (matched case-insensitively, ignoring surrounding whitespace and a trailing
period), `evaluate` SHALL consume the decision as a repo-less confirmation: it SHALL stamp
`repo-less: confirmed` in the brief's frontmatter, append a `verdict: repo-less-confirmed`
triage note with rule `decision`, archive the decision (clearing the brief's
`awaiting-decision` link), leave `repo:` empty, and keep the brief in the repo-less group. The
brief SHALL NOT be reported as an inferred repo nor as an unresolvable repo assignment. Any
other canonical answer that does not resolve to an on-disk checkout SHALL still be reported
as unresolvable with brief and decision untouched.

A repo-less brief carrying `repo-less: confirmed` SHALL NOT be escalated to a
`needs-decision` verdict with the canonical repo-assignment question; the repo-less
escalation rule SHALL treat it as not due.

#### Scenario: Proceed-as-scoped answer is consumed
- **WHEN** a brief with `repo: null` links an answered decision whose question is "Which repo
  should this brief target?" and whose answer is "Proceed with the brief as currently scoped"
- **THEN** the brief gains `repo-less: confirmed` and a `verdict: repo-less-confirmed` note
  with rule `decision`, its `awaiting-decision` link is cleared, the decision is archived,
  `repo:` is still empty, the brief is grouped under the repo-less group, and neither the
  inferred nor the unresolvable list names it

#### Scenario: Answer matching is lenient on case and trailing period
- **WHEN** the answer is "  proceed with the brief as currently scoped. "
- **THEN** the decision is consumed exactly as for the verbatim option text

#### Scenario: Other unresolvable canonical answer is still reported
- **WHEN** the canonical question's answer is "the nonesuch repo" and no such checkout exists
- **THEN** the brief and decision are unchanged and the brief is reported as an unresolvable
  repo assignment

#### Scenario: Confirmed repo-less brief is not re-asked
- **WHEN** a repo-less brief carrying `repo-less: confirmed` exceeds the queue-age or
  keep-count escalation limits
- **THEN** no `needs-decision` verdict with the repo-assignment question is issued for it and
  no decision record is filed or re-opened

#### Scenario: Unconfirmed repo-less brief still escalates
- **WHEN** a repo-less brief without `repo-less: confirmed` is due for escalation
- **THEN** it receives a `needs-decision` verdict with the repo-assignment question, as today
