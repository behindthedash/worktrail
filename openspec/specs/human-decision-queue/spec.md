# human-decision-queue Specification

## Purpose
Lets an unattended run hand a genuine product decision to a human as a structured, answerable
record — releasing the blocked brief instead of stranding it — and lets the next unattended pass
consume the answer and continue from the blocked point, with guardrails that keep decision
filing from becoming a laziness escape hatch.
## Requirements
### Requirement: Decision records are structured and directory-arbitrated

`worktrail-decision` SHALL store records under the work-queue root at
`decisions/open/`, `decisions/answered/`, and `decisions/resolved/`, where the containing
directory — not the `status:` frontmatter field — is the arbiter of a record's state, so a
human who answers by editing the `## Answer` section and moving the file by hand is honored.
`ask` SHALL refuse a record missing a non-empty question, plain-English background,
why-this-is-a-product-decision, what-was-attempted context, or fewer than two options, and
SHALL refuse a second open decision for the same brief. The rendered record SHALL carry the
background as its own section, SHALL present options in the agent's priority order with a note
that the human may answer by number or write their own direction, and — when per-option costs
are supplied — SHALL label each option with its cost. Per-option costs SHALL be index-matched
to options: supplying a different count than the number of options SHALL be refused.

#### Scenario: A record is hand-moved to answered/ with a stale status field
- **WHEN** a record whose frontmatter still reads `status: open` sits in `decisions/answered/`
- **THEN** its status resolves as `answered`

#### Scenario: A lazy ask with one option
- **WHEN** `ask` is invoked with a single `--option`
- **THEN** it is refused with an error explaining at least two options are required

#### Scenario: A second open decision for the same brief
- **WHEN** `ask --brief <id>` is invoked while that brief already has an open decision
- **THEN** it is refused

#### Scenario: An ask without a plain-English background
- **WHEN** `ask` is invoked with a missing or empty `--background`
- **THEN** it is refused

#### Scenario: Options labeled with costs
- **WHEN** `ask` is invoked with two options and two `--option-cost` values
- **THEN** the rendered record lists each option in the given priority order with its cost
  beneath it

#### Scenario: Cost count does not match option count
- **WHEN** `ask` is invoked with two options and one `--option-cost`
- **THEN** it is refused with an error naming the mismatch

### Requirement: Filing a decision blocks and releases the source brief

`ask --brief <id> --release` SHALL stamp `awaiting-decision: <decision-id>` on the brief and
release it back to `queue/`. `work_queue.list` SHALL report a brief whose linked decision is
still open as `blocked` (exposing `awaiting_decision` and `decision_status` fields), SHALL
report it unblocked once the decision is answered, and SHALL treat a decision id that resolves
to no record as unblocked so a deleted record never wedges the brief. Explicitly claiming a
brief whose decision is still open SHALL warn but not hard-block.

#### Scenario: Brief blocked while the decision is open
- **WHEN** a brief carries `awaiting-decision:` naming a record in `decisions/open/`
- **THEN** `list` reports it `blocked: true` with `decision_status: open`

#### Scenario: Brief unblocks when the human answers
- **WHEN** the linked record moves to `decisions/answered/`
- **THEN** `list` reports the brief `blocked: false` with `decision_status: answered`

#### Scenario: The decision record was deleted
- **WHEN** a brief's `awaiting-decision:` id resolves to no record in any status directory
- **THEN** `list` reports the brief unblocked

### Requirement: Answer and resolve close the loop

`answer <id> --answer <text>` SHALL write the text into the record's `## Answer` section, stamp
`answered-at`, and move it to `answered/`. `resolve <id>` SHALL move an answered record to
`resolved/`, stamp `resolved-at`, and strip the linked brief's `awaiting-decision` field;
resolving a still-open record SHALL be refused so a pending question is never silently
discarded.

#### Scenario: Resolving an open decision
- **WHEN** `resolve` is invoked on a record still in `decisions/open/`
- **THEN** it is refused with a `still-open` result

#### Scenario: Resolving an answered decision with a linked brief
- **WHEN** `resolve` is invoked on an answered record whose brief still exists
- **THEN** the record moves to `decisions/resolved/` and the brief's `awaiting-decision`
  frontmatter field is removed

### Requirement: The drain rewards filed decisions and punishes decision-less blocks

`worktrail-drain` SHALL detect whether a `blocked_product_decision` iteration filed at least one
new open decision during that iteration. A decision-filed block SHALL NOT count toward the
consecutive-failure circuit breaker and SHALL be logged with the filed ids; a decision-less
block SHALL count exactly as before. The run summary SHALL carry each iteration's
`decisions_filed` list and a `decisions_open` total, and the end-of-run log SHALL tell the
operator how to review and answer open decisions.

#### Scenario: Repeated blocks that each filed a decision
- **WHEN** consecutive iterations finish `blocked_product_decision` and each filed a new open
  decision
- **THEN** the circuit breaker does not trip and the drain continues to the next ready brief

#### Scenario: Repeated blocks that filed nothing
- **WHEN** consecutive iterations finish `blocked_product_decision` without filing any decision
- **THEN** the circuit breaker trips at its configured threshold exactly as before

### Requirement: Open decisions surface as an interactive dashboard picker category

`worktrail-dashboard` SHALL accept open decision records as an input (`--decisions-json`, the
JSON shape of `worktrail-decision list --status open --json`) and, when at least one decision is
open, SHALL include a `decisions` category in `category_actions` labeled `Open decisions (N)`
where N is the open count. The `decisions` category SHALL be ranked ahead of `ready` in
`category_actions` so it is never displaced by active spec work under the existing ≤4-category
cap; when the addition of `decisions` would push the total above four, the `new-work` category
SHALL be the one omitted rather than any other populated category. `category_items["decisions"]`
SHALL list each open decision as a `type: "decision"` item carrying `action: "answer-decision"`,
the decision `id`, a label derived from its question, and its `repo`/`brief` fields when present.
When no decision is open, `category_actions`/`category_items` SHALL be unchanged from their
current behavior (no `decisions` category, no `decisions` key).

#### Scenario: Open decisions add a picker category
- **WHEN** `worktrail-dashboard` is invoked with `--decisions-json` naming two open decisions
- **THEN** `category_actions` includes a `decisions` entry labeled `Open decisions (2)`, and
  `category_items["decisions"]` lists two `type: "decision"` items

#### Scenario: No open decisions leaves the picker unchanged
- **WHEN** `worktrail-dashboard` is invoked with no `--decisions-json` or an empty decision list
- **THEN** `category_actions` carries no `decisions` entry and `category_items` carries no
  `decisions` key

#### Scenario: A full category set omits new-work, never an existing category
- **WHEN** open decisions, ready specs, tasking-needed specs, and workqueue items are all
  simultaneously present
- **THEN** `category_actions` contains `decisions`, `ready`, `needs-tasks`, and `workqueue`, and
  omits `new-work`

### Requirement: Selecting an open decision answers it interactively without a manual CLI call

Selecting a `type: "decision"` item SHALL drive an interactive flow that presents the decision's
question, background, and priority-ordered options (including any per-option cost) via an
interactive choice prompt, with a free-text fallback for a direction not among the listed
options. Recording the human's choice SHALL invoke `worktrail-decision answer <id> --answer
"..."` with the full text of the selected option (or the typed free text), which — per the
existing decision-queue behavior — moves the record to `answered/` and unblocks any linked
brief. This flow SHALL NOT require the human to invoke any `worktrail-decision` subcommand by
hand, and SHALL NOT itself resolve the decision — resolution remains the consuming agent's
responsibility when it later resumes the unblocked brief, unchanged from the existing
agent-side procedure.

#### Scenario: Answering from the picker unblocks the linked brief
- **WHEN** a human selects an open decision item and picks one of its listed options
- **THEN** `worktrail-decision answer <id> --answer "<option text>"` is invoked with that
  option's full text, and the decision's linked brief is no longer blocked

#### Scenario: Answering with free text
- **WHEN** a human selects an open decision item and provides a typed answer instead of a listed
  option
- **THEN** `worktrail-decision answer <id> --answer "<typed text>"` is invoked with that text
  verbatim

#### Scenario: Answering does not resolve the decision
- **WHEN** a human answers an open decision through the picker
- **THEN** the record moves to `decisions/answered/`, not `decisions/resolved/`, and remains
  awaiting the consuming agent's `resolve` step

