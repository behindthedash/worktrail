## ADDED Requirements

### Requirement: CI watch follows the PR head across bot pushes
The CI watch SHALL record the PR head SHA it is watching before each blocking watch and
re-read it after every non-zero watch exit. When the head SHA has changed, the watch SHALL
treat the exit as superseded: it SHALL NOT count it against the re-issue budget, SHALL NOT
classify that exit's failing rows, and SHALL re-enter the blocking watch against the new head.
The number of head changes followed within one watch SHALL be bounded; beyond the bound the
watch SHALL report budget exhausted exactly as today. When the head SHA cannot be read, the
watch SHALL behave as it does today.

#### Scenario: Bot push cancels the first run set
- **WHEN** the blocking watch exits non-zero and the PR head SHA differs from the one
  recorded before that watch
- **THEN** the re-issue counter is not incremented, no check rows from that exit are
  classified, and the watch re-enters against the new head

#### Scenario: Watch settles on the new head
- **WHEN** the watch re-entered after a head change and the blocking watch then exits zero
- **THEN** the result is settled with no failing checks and budget not exhausted

#### Scenario: Head keeps moving past the follow bound
- **WHEN** the head SHA changes more times than the follow bound within one watch
- **THEN** the watch returns budget exhausted

#### Scenario: Stable head still exhausts the re-issue budget
- **WHEN** the head SHA is unchanged and the blocking watch exits non-zero with no failing
  rows more than the re-issue bound allows
- **THEN** the watch returns budget exhausted as before this change

#### Scenario: Head SHA read fails
- **WHEN** the head SHA query fails or returns no SHA
- **THEN** the exit counts against the re-issue budget and is classified as before this
  change

### Requirement: Push rebases onto bot-only remote commits
Before pushing, the pipeline SHALL fetch the remote branch and compare it with the local
head. When the remote branch is ahead and every remote-only commit has a bot author, the
pipeline SHALL rebase the local branch onto the remote head and then push. When any
remote-only commit has a non-bot author, the pipeline SHALL NOT rebase and SHALL let the push
report its own result. When the rebase fails, the pipeline SHALL abort the rebase, restore the
working tree, and refuse at the push step quoting git's reason. When the remote branch does
not exist yet, or the comparison cannot be made, the pipeline SHALL proceed to the push
unchanged.

#### Scenario: Remote is ahead by a bot commit only
- **WHEN** the fetched remote branch contains commits not in the local head and each has a
  bot author
- **THEN** the local branch is rebased onto the remote head before the push runs, and the
  push is issued from the rebased head

#### Scenario: Remote is ahead by a human commit
- **WHEN** the fetched remote branch contains a commit not in the local head whose author is
  not a bot
- **THEN** no rebase runs and the push proceeds, refusing as non-fast-forward with git's
  detail as it does today

#### Scenario: Rebase conflicts
- **WHEN** rebasing onto the bot-only remote head exits non-zero
- **THEN** the rebase is aborted, nothing is pushed, and the outcome is a push refusal whose
  detail quotes the rebase output

#### Scenario: First push of a new branch
- **WHEN** the fetch reports that the remote branch does not exist
- **THEN** no rebase runs and the push proceeds as before this change

#### Scenario: Local already contains the remote head
- **WHEN** the fetched remote branch has no commits missing from the local head
- **THEN** no rebase runs and the push proceeds as before this change
