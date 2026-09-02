## Purpose

Lets a repo make per-task review cost proportional to change size: a small implementation diff whose tests passed skips the review spawn, recorded in the journal so a resume never re-reviews or loses the verdict.

## ADDED Requirements

### Requirement: Small verified diffs skip the review spawn
When the policy key `review_skip_max_diff_lines` is greater than 0, the orchestrator SHALL skip the review spawn for a task's first review when the implement report has `status: success` and `tests: passed` and the task's commit diff against its base commit, counting added plus removed lines and excluding test files, is under the threshold. The fast path SHALL NOT apply to a review that follows a fix, and SHALL NOT apply when the implement report's `tests` is anything other than `passed`.

#### Scenario: Small passing diff skips review
- **WHEN** the threshold is 40, the implement report says tests passed, and the diff excluding `tests/` is 12 lines
- **THEN** no review worker is spawned and the task advances as if reviewed

#### Scenario: Tests not passed never skips
- **WHEN** the threshold is 40, the diff is 5 lines, and the implement report says `tests: none`
- **THEN** a review worker is spawned

#### Scenario: Post-fix review is never skipped
- **WHEN** a task's review failed once, the fix committed a 3-line change, and the threshold is 40
- **THEN** the follow-up review worker is spawned

### Requirement: Small-diff skip is recorded and resumable
A skipped review SHALL append a review-role journal entry with `review_status: skipped-small-diff` and a note carrying the counted line total, and the task state transition SHALL treat that verdict as a passed review, including when a journal is replayed on resume.

#### Scenario: Journal records the skip
- **WHEN** a review is skipped for a small diff
- **THEN** the journal's review entry for that task has `review_status: skipped-small-diff`

#### Scenario: Resume honors a recorded skip
- **WHEN** a run is resumed from a journal whose review entry for a task is `skipped-small-diff`
- **THEN** the task resumes past review without spawning a review worker

### Requirement: Review fast path is disabled by default
`review_skip_max_diff_lines` SHALL default to 0, which disables the fast path, and SHALL be forced back to 0 with a warning when it is not a non-boolean integer of at least 0.

#### Scenario: Unset key changes nothing
- **WHEN** a repo's policy does not set `review_skip_max_diff_lines`
- **THEN** every non-exempt task is reviewed exactly as before

#### Scenario: Invalid value falls back
- **WHEN** a policy file sets `review_skip_max_diff_lines: "forty"`
- **THEN** loading the policy yields 0 for that key and records a warning naming it
