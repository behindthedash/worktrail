## ADDED Requirements

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
