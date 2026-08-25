## Purpose

Deterministically flags a session's own self-named `deferred_work` run-record entries
that have no matching work-queue handoff, at Stop-hook time, independent of and additive
to the hook's existing agent-judged EXCEPTIONAL-VALUE gate.

## Requirements

### Requirement: Additive And Non-Interfering
The system SHALL implement the deferral flag as a check distinct from the Stop hook's
existing EXCEPTIONAL-VALUE gate. The system SHALL NOT alter the EXCEPTIONAL-VALUE gate's
instruction text or the conditions under which it fires.

#### Scenario: EXCEPTIONAL-VALUE gate output is unchanged
- **WHEN** a session ends with substantive work and no unmatched `deferred_work` entry
- **THEN** the hook's printed instruction is byte-for-byte identical to the
  EXCEPTIONAL-VALUE-gate-only instruction produced before this change existed

#### Scenario: Both checks can fire in the same session
- **WHEN** a session ends with substantive work and an unmatched `deferred_work` entry
- **THEN** the hook's output includes the unmodified EXCEPTIONAL-VALUE gate instruction
  plus an additional, separate deferral-flag block

### Requirement: Run-Record Discovery Via Transcript Grep
The system SHALL discover this session's run record(s) by extracting
`~/.worktrail/runs/**/*.yaml` path literals referenced in the session's Claude Code
transcript file — the same transcript file the hook already reads once for its
substantive-work check. The system SHALL NOT require a `session_id` field on the
run-record schema to perform this discovery.

#### Scenario: Run record discovered from a printed path literal
- **WHEN** the transcript contains a bash command or tool output that echoes a run-record
  path matching `~/.worktrail/runs/**/*.yaml`
- **THEN** that run record is read for its `deferred_work` entries

#### Scenario: No run-record path literal in transcript
- **WHEN** the transcript contains no `~/.worktrail/runs/**/*.yaml` path literal
- **THEN** the deferral flag performs no further work and the hook's output is unchanged
  from today

### Requirement: Deferred-Work-Only Signal Source
The system SHALL read only the `deferred_work` list of a discovered run record as its
source of deferral signal. The system SHALL NOT read or match against the run record's
`scope_review` entries, and SHALL NOT scan a pull request body or invoke the `gh` CLI.

#### Scenario: Scope-review entry never triggers a flag
- **WHEN** a run record's `scope_review` list contains an entry recording
  `out-of-scope | <item> | different purpose: ...`, and its `deferred_work` list is empty
- **THEN** the deferral flag does not fire for that entry

#### Scenario: Deferred-work entry is the only scanned source
- **WHEN** a run record has one or more `deferred_work` entries
- **THEN** only those entries are evaluated for deferral-phrase matching

### Requirement: Deferral-Phrase Matching
The system SHALL match each `deferred_work` entry's text against a narrow, explicit,
extensible list of deferral phrases (e.g. "advisory for now", "deferred", "once
calibrated", "follow-up", "in a later PR"). Only entries matching at least one phrase are
candidates for flagging.

#### Scenario: Matching entry becomes a candidate
- **WHEN** a `deferred_work` entry's text contains "keep this advisory for now"
- **THEN** that entry is a candidate for the handoff cross-check

#### Scenario: Non-matching entry is never flagged
- **WHEN** a `deferred_work` entry's text contains none of the configured deferral
  phrases
- **THEN** that entry is never surfaced by the deferral flag, regardless of whether a
  covering handoff exists

### Requirement: Handoff Cross-Check Before Flagging
For each phrase-matching `deferred_work` candidate, the system SHALL check whether an
existing work-queue handoff brief in `queue/` or `picked/` already covers it, using the
same bounded probe-extraction-and-search approach `check_brief_staleness.py` uses to
compare free text against other content. The system SHALL flag a candidate only when no
matching handoff is found.

#### Scenario: Candidate with a matching handoff is not flagged
- **WHEN** a phrase-matching `deferred_work` candidate's extracted probes match an
  existing brief in `queue/` or `picked/`
- **THEN** the deferral flag does not surface that candidate

#### Scenario: Candidate with no matching handoff is flagged
- **WHEN** a phrase-matching `deferred_work` candidate's extracted probes match no brief
  in `queue/` or `picked/`
- **THEN** the deferral flag surfaces that candidate

### Requirement: Silent When Nothing Unmatched
The system SHALL print no additional instruction text when every `deferred_work` entry
either fails phrase matching or has a matching handoff. In that case the hook's behavior
SHALL be identical to its behavior before this change.

#### Scenario: No candidates at all
- **WHEN** a discovered run record's `deferred_work` list is empty or matches no
  deferral phrase
- **THEN** the hook prints no deferral-flag instruction

### Requirement: Fail-Open And Headless-Excluded
The system SHALL perform the deferral flag's discovery, extraction, phrase matching, and
handoff cross-check without ever raising an exception that terminates the Stop hook, and
SHALL NOT run when `CC_HEADLESS=1`.

#### Scenario: Unreadable or malformed run record does not break the hook
- **WHEN** a discovered run-record path is unreadable or contains malformed YAML
- **THEN** the deferral flag is skipped for that record and the hook still exits
  successfully

#### Scenario: Headless sessions are never checked
- **WHEN** the environment variable `CC_HEADLESS` is set to `1`
- **THEN** the deferral flag performs no work, matching the existing hook-wide headless
  exclusion
