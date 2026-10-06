# work-queue-external-blocker Specification

## Purpose
Give a queued brief a way to declare a blocker that is not a queue-brief prerequisite and has
no known date -- a dependency in another repository, an upstream pull request or branch that
must land, an out-of-band operator action -- so a valid-but-blocked brief leaves the
automatic-pick pool instead of being picked and re-investigated on every pass, while a human
can still see why it is held and clear it when the blocker lifts.
## Requirements
### Requirement: A brief can declare an external blocker

`worktrail-work-queue` SHALL recognize an optional `blocked-on:` frontmatter field on a queued
brief: a non-empty single-line free-text description of a blocker that cannot be expressed as a
`blocked-by` reference (a list of queue-brief IDs) or a `next-check-after` date (a calendar
date). A brief whose `blocked-on:` is present and non-empty SHALL be reported blocked in
queue-listing data -- its `blocked` flag true -- and that listing SHALL expose the
`blocked-on:` value, so the brief leaves every ready count and the automatic-pick eligible
pool. A brief with no `blocked-on:` field, or with a value that is empty or whitespace-only,
SHALL be unaffected. The field SHALL be treated as an independent blocking reason alongside a
queue-brief `blocked-by` prerequisite and an open `awaiting-decision`, and like `blocked-by` it
SHALL NOT by itself refuse an explicit interactive claim.

#### Scenario: A brief with an external blocker is reported blocked

- **WHEN** queue-listing data is produced for a queued brief carrying
  `blocked-on: devops PR #42 must land before this can be dispatched`
- **THEN** that brief's `blocked` flag is true, its listing entry exposes that blocker text, and
  the brief is not counted as ready

#### Scenario: A brief without the field is unaffected

- **WHEN** queue-listing data is produced for a queued brief that carries no `blocked-on:` field
- **THEN** its `blocked` flag is determined exactly as before this requirement

#### Scenario: An empty or whitespace-only value declares nothing

- **WHEN** a queued brief carries `blocked-on: "   "`
- **THEN** the field is treated as absent and the brief is not reported blocked on its account

#### Scenario: External and dependency blocks coexist

- **WHEN** a queued brief carries both an unsatisfied `blocked-by` prerequisite and a non-empty
  `blocked-on:`
- **THEN** the brief is reported blocked (as it already was for the prerequisite) and its
  listing also exposes the external blocker

#### Scenario: An explicit claim of an externally-blocked brief still succeeds

- **WHEN** an operator explicitly claims a queued brief carrying `blocked-on:`
- **THEN** the claim succeeds exactly as it does for a `blocked-by`-blocked brief

### Requirement: Automatic selection names the external blocker and never picks it

When automatic selection skips a brief because of a non-empty `blocked-on:`, it SHALL record
the stable structured skip reason `blocked:external`, retaining `blocked` as the leading coarse
category so existing miss-log aggregation continues to bucket it as blocked, and SHALL never
return that brief as the pick. When a brief carries both a dependency-reference problem and a
`blocked-on:`, the dependency-reference reason SHALL take precedence: `blocked:malformed-dependency`
outranking `blocked:ambiguous-dependency`, and both outranking `blocked:external`, because a
malformed or ambiguous reference is the strictly more broken value.

#### Scenario: An externally-blocked brief is skipped with the external reason

- **WHEN** automatic selection evaluates a queued brief whose only blocking reason is a
  non-empty `blocked-on:`
- **THEN** the brief is skipped and its recorded reason is `blocked:external`

#### Scenario: The external blocker never becomes the pick

- **WHEN** the queue contains only briefs that each carry a non-empty `blocked-on:`
- **THEN** automatic selection returns no pick and records each with reason `blocked:external`

#### Scenario: A clean brief in the same queue is still pickable

- **WHEN** the same queue also contains an eligible brief with no blocker
- **THEN** automatic selection returns that brief as the pick

#### Scenario: A malformed reference outranks the external blocker

- **WHEN** a brief carries both a malformed `blocked-by` reference and a non-empty `blocked-on:`
- **THEN** its recorded skip reason is `blocked:malformed-dependency`, not `blocked:external`

#### Scenario: Miss-log aggregation still counts these as blocked

- **WHEN** automatic selection finds nothing to pick and records why
- **THEN** a brief skipped for its external blocker is counted under the same coarse `blocked`
  category as other blocked briefs, while its per-brief entry retains `blocked:external`

### Requirement: Operator output and claim warnings name the external blocker

Human-readable queue listing and the claim path SHALL name a brief's external blocker so an
operator can see why the brief is held without inspecting stored files. The human listing's
blocked section SHALL show the `blocked-on:` text for each blocked brief that carries one, and
a successful claim (or batch claim) of a brief carrying `blocked-on:` SHALL include a warning
naming that blocker in its `warnings` list, in the same way an unresolved `blocked-by`
prerequisite is surfaced. Neither SHALL refuse the operation.

#### Scenario: Human queue listing shows the external blocker

- **WHEN** an operator lists the queue in human-readable form and a queued brief carries
  `blocked-on: waiting on the acme-api token refactor`
- **THEN** the blocked section shows that brief and its blocker text

#### Scenario: Claiming an externally-blocked brief warns

- **WHEN** a brief carrying `blocked-on:` is claimed
- **THEN** the claim succeeds and its result's `warnings` include one naming that blocker

### Requirement: An external blocker is cleared by an explicit command

`worktrail-work-queue unblock <identifier>` SHALL remove the named brief's `blocked-on:` field,
restoring its eligibility for automatic selection, and SHALL be idempotent: a brief that
carries no `blocked-on:` is reported as having nothing to clear and left unchanged. The command
SHALL NOT remove a `blocked-by` prerequisite or modify any other frontmatter, and it SHALL
report the brief it acted on (its id and whether a blocker was cleared), like the other
`worktrail-work-queue` subcommands.

#### Scenario: Unblock clears the blocker

- **WHEN** `worktrail-work-queue unblock <id>` is run for a queued brief carrying `blocked-on:`
- **THEN** the brief's `blocked-on:` field is removed, its other frontmatter is unchanged, and
  the command reports that the blocker was cleared

#### Scenario: Unblock is idempotent

- **WHEN** `worktrail-work-queue unblock <id>` is run for a brief that carries no `blocked-on:`
- **THEN** the brief is unchanged and the command reports that there was no blocker to clear

#### Scenario: Unblock never touches a blocked-by prerequisite

- **WHEN** `worktrail-work-queue unblock <id>` is run for a brief carrying both `blocked-by:`
  and `blocked-on:`
- **THEN** only the `blocked-on:` field is removed, and the brief remains blocked by its
  `blocked-by` prerequisite

#### Scenario: An unblocked brief becomes auto-pickable again

- **WHEN** a brief skipped with reason `blocked:external` has its `blocked-on:` cleared by
  `unblock` and automatic selection runs again over a queue with no other eligible brief
- **THEN** the brief is no longer skipped for an external blocker -- its listing `blocked` flag
  is false and it is eligible to be returned as the pick

