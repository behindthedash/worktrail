## MODIFIED Requirements

### Requirement: Run record is completed with a real state

Every landing SHALL stamp the pull request URL and merge result on a run record and finish it
with one of the allowed completion states, or — in checkpoint mode, for a PR opened mid-run
that the calling route continues past — append the same outcome as a decision entry instead
of finishing. A caller with no run record SHALL have one started for it by the pipeline,
carrying the caller's route and request summary, so that no landing is unrecorded. For a run
record the pipeline started itself, the pipeline SHALL also record the scope review that
`finish`'s scope-completeness gate requires -- one `complete` entry whose item is the request
summary and whose evidence names the pushed commit, branch, and pull request URL -- before
finishing with an implementation-completion state; a caller-supplied run record SHALL NOT have
a scope-review entry added on its behalf. When the run record cannot be finished, the landing
result SHALL carry the run-record tool's own failure message as its detail, whether the tool
wrote that message to stdout or to stderr; a failure whose message was printed on either
stream SHALL never surface as an empty detail.

#### Scenario: Caller supplies a run record

- **WHEN** a landing is invoked with an existing run record
- **THEN** that record gains the pull request URL and merge result and is finished with the
  classified state, and no scope-review entry is appended by the pipeline

#### Scenario: Caller has no run record

- **WHEN** a landing is invoked without a run record
- **THEN** the pipeline starts one for the caller's repository and route before landing, and
  the result names its path

#### Scenario: Pipeline-started record finishes with a real state

- **WHEN** a landing is invoked without a run record and the pull request reaches an all-pass,
  merged, or branch-protection-blocked outcome
- **THEN** the pipeline appends one `complete` scope-review entry for the request summary,
  citing the pushed commit, branch, and pull request URL, and the record is finished with
  `completed_pr_open`, `completed_and_merged`, or `blocked_product_decision` respectively
  rather than being reported as a ceiling with `failed_recoverable`

#### Scenario: Checkpoint mode

- **WHEN** a landing is invoked in checkpoint mode and the outcome is all-pass
- **THEN** the run record gains a decision entry describing the outcome and is not finished,
  and no scope-review entry is appended

#### Scenario: Run record cannot be finished

- **WHEN** finishing the run record fails for any reason
- **THEN** the landing is reported as a ceiling with `failed_recoverable`, its merge result
  still reads "... but run record could not be completed", and its detail contains the
  run-record tool's failure message, whether the tool wrote it to stdout or to stderr
