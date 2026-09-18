## ADDED Requirements

### Requirement: Interactive single-brief pickup honours linked decisions
When `worktrail-go <brief-id>` evaluates a single intake brief, the system SHALL first run the
same decision-consumption pre-pass the unattended inventory runs — consuming an answered
repo-assignment or re-home decision (a resolved repo overriding any repo passed on the
command line) and otherwise consuming an answered decision as answered guidance — regardless
of whether a repo was supplied. If, after that pre-pass, the brief still links a decision
that is `open` or `answered`, the system SHALL NOT evaluate the brief: the single-brief
evaluate command SHALL print `null`, write a `blocked_pending_decision: <decision-id>
(<status>)` line to stderr, and exit 2, leaving the brief and decision untouched. The
`worktrail-go` skill text SHALL document this exit as a case that does not proceed to apply.

#### Scenario: Answered keep decision is consumed before evaluation
- **WHEN** `worktrail-go` evaluates a brief with `repo: /repos/worktrail`, passing that repo,
  and the brief links an answered decision whose answer is "keep under worktrail, contract
  change"
- **THEN** a `decision-answered` note is written, the decision is archived, and the evaluator
  is spawned for the brief with the answer in its prompt; no new decision asking the same
  question is filed

#### Scenario: Open decision blocks the single-brief evaluate
- **WHEN** `worktrail-go` evaluates a brief whose linked decision is still `open`
- **THEN** no evaluator is spawned, the command prints `null`, writes
  `blocked_pending_decision: <decision-id> (open)` to stderr, and exits 2

#### Scenario: Answered re-home decision overrides the passed repo
- **WHEN** `worktrail-go` evaluates a brief passing `/repos/worktrail` as its repo, and the
  brief links an answered decision whose answer is "move it to the devops repo" with a
  `devops` checkout under the repos root
- **THEN** the brief's `repo:` becomes the `devops` checkout path and the evaluator runs
  against that checkout, not `/repos/worktrail`
