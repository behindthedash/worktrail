# queue-triage-rejected-closure-recovery Specification

## Purpose

Close a brief that a rejected closure left claimed in `picked/` after its landing pull request
already exists, using the landing that `apply` recorded on the brief, so the triage path always
has a closer and never needs a human to reconstruct which PR a brief's work went to.

## ADDED Requirements

### Requirement: One command closes a rejected-closure brief against its recorded landing
The system SHALL provide a `worktrail-queue-triage recover-closure` command taking a brief
identifier and an optional closure note. For a brief claimed in `picked/` with `status: picked`
that carries a recorded landing (the `closure-rejected-pr` and `closure-rejected-reason` fields
`apply` writes when a closure is rejected after a pull request exists), the command SHALL
re-attempt the closure as `done(brief_id, triaged_to=<recorded pull request URL>,
note=<the supplied note or none>)`, so the brief closes as a triage closure against the pull
request that already exists. The supplied note SHALL be omitted when none is given, and the
command SHALL NOT synthesize, default, or reuse a closure note of its own. The command SHALL NOT
release the brief, SHALL NOT re-enter the triage evaluation, SHALL NOT open, close, or modify a
pull request, and SHALL spawn no agent.

#### Scenario: An ordinary rejected closure recovers in one command
- **WHEN** a brief sits in `picked/` with `status: picked`, `closure-rejected-pr` naming a pull
  request URL, and `closure-rejected-reason` naming the rejection, and
  `worktrail-queue-triage recover-closure --brief <id>` runs
- **THEN** the closure completes with `triaged-to` stamped to that pull request URL, the brief's
  `status` becomes `done`, the command exits 0, and no note is passed to the closure

#### Scenario: A supplied note is used verbatim
- **WHEN** the command runs with a closure note supplied by the operator
- **THEN** that exact note is passed to the closure and the command reports the closure's outcome

#### Scenario: Recovery reports the outcome for a machine caller
- **WHEN** the command runs with its JSON output selected
- **THEN** it prints one object carrying the brief id, the outcome, the recorded pull request URL,
  the recorded rejection reason, and the closure's own status and error when the closure refused

#### Scenario: Recovery never re-queues the brief
- **WHEN** recovery completes, and again when it refuses
- **THEN** the brief is in `picked/` in no case less than it was before the command, and it is
  never present in `queue/`

#### Scenario: A dry run reports the plan and changes nothing
- **WHEN** the command runs in dry-run mode against a recoverable brief
- **THEN** no closure is attempted, the brief is byte-identical, and the output names the recorded
  pull request URL and the note that would be used

### Requirement: Recovery refuses everything it cannot close, leaving the brief unchanged
Before attempting any closure the command SHALL refuse, leave the brief byte-identical, and exit
non-zero when: the identifier resolves in neither `picked/` nor `queue/`; the identifier resolves
to a brief that is not claimed in `picked/` with `status: picked`; the brief carries no recorded
landing; or the recorded landing value is not a pull request URL. When the closure itself is
refused again (a gate on the closure still refuses the note), the command SHALL surface that
closure's own status and message, leave the brief byte-identical, and exit non-zero. Every
refusal SHALL name the brief and the specific reason.

#### Scenario: An unknown brief is refused
- **WHEN** the command runs with an identifier that resolves to no brief in `picked/` or `queue/`
- **THEN** nothing is written and the command exits non-zero naming the unresolvable identifier

#### Scenario: An unclaimed brief is refused
- **WHEN** the identifier resolves to a brief still in `queue/`, or to a claimed brief whose
  `status` is not `picked`
- **THEN** the command refuses, names that state, and writes nothing

#### Scenario: A brief with no recorded landing is refused
- **WHEN** the identifier resolves to a picked brief carrying no `closure-rejected-pr` field
- **THEN** the command refuses naming the missing recorded landing, writes nothing, and exits
  non-zero

#### Scenario: A recorded landing that is not a pull request URL is refused
- **WHEN** a brief's `closure-rejected-pr` value does not parse as a pull request URL
- **THEN** the command refuses naming that value, writes nothing, and exits non-zero

#### Scenario: A still-refused closure surfaces the gate's demand
- **WHEN** the closure refuses again because the note does not satisfy a closure gate
- **THEN** the command reports that closure status and its message, leaves the brief
  byte-identical, and exits non-zero

#### Scenario: Recovery is idempotent after success
- **WHEN** the command runs a second time against a brief whose closure already completed
- **THEN** it refuses because the brief is no longer a picked brief with an unclosed recorded
  landing, and writes nothing

### Requirement: The dashboard surfaces a rejected-closure brief as a recovery action
The dashboard's stalled-in-flight scan SHALL read a picked brief's recorded landing and carry it
into the returned entry, and SHALL surface a brief carrying a recorded landing regardless of how
recently it was claimed. The picker's item for such a brief SHALL report `recover-closure` as its
action and SHALL name the recorded pull request in its description, so the front door offers the
recovery rather than a resume. A picked brief with no recorded landing SHALL keep today's
freshness filter and its `resume` action unchanged.

#### Scenario: A rejected-closure brief is offered as a recovery
- **WHEN** a picked brief carries a recorded landing and the picker builds its workqueue items
- **THEN** that brief's item reports `action: "recover-closure"` and its description names the
  recorded pull request URL

#### Scenario: A freshly recorded rejection is not hidden as freshly claimed
- **WHEN** a picked brief carrying a recorded landing was claimed less than the staleness window
  ago
- **THEN** the scan still returns it, so the outstanding closure is visible immediately

#### Scenario: An abandoned session is still a resume
- **WHEN** a picked brief past the staleness window carries no recorded landing
- **THEN** it is returned with `action: "resume"` and no recorded-landing field, exactly as today

### Requirement: Operator references point at the composed recovery command
The front door's action table SHALL map the `recover-closure` action to the
`worktrail-queue-triage recover-closure` invocation, and the dashboard reference SHALL list
`recover-closure` among the actions an item may carry and describe the recorded landing on an
in-flight entry. The interactive triage step SHALL name the recovery for the case its own apply
produces: a landing entry reporting a rejected closure with a pull request URL.

#### Scenario: The action table names the recovery
- **WHEN** the front door's action-to-dispatch table is consulted for the `recover-closure` action
- **THEN** it names the `worktrail-queue-triage recover-closure` command and the evidence re-run it
  may require

#### Scenario: The dashboard reference lists the action
- **WHEN** the dashboard reference enumerates the `action` values a picker item may carry
- **THEN** `recover-closure` is among them and the in-flight entry's recorded landing is described
