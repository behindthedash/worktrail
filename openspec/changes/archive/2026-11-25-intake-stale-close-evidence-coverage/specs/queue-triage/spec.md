## ADDED Requirements

### Requirement: Stale-close verdicts account for every reported failure

A brief's `focus:` text SHALL be treated as a set of independently reported failures, derived
deterministically by splitting it into top-level items: at sentence-ending punctuation followed
by whitespace, at semicolons, at commas followed by whitespace, at list-item line breaks, and at
the stand-alone word `and`. Each item SHALL be whitespace-collapsed, stripped of surrounding
punctuation, de-duplicated against earlier items, and omitted when shorter than 12 characters.

A `stale-close` verdict SHALL carry `failure_coverage`: a list of `{failure, evidence}` entries,
where each `failure` is at least 12 characters copied verbatim from the brief's focus text apart
from whitespace normalization and occurs within one or more focus items, collectively covering
every item, and each `evidence` is non-empty and states the specific repair, commit, pull
request, file, or archived-repo fact that resolves that item. A `stale-close` verdict that leaves
any focus item uncovered SHALL NOT claim or close the brief: both preview and apply SHALL report
a downgrade to `keep`, the brief SHALL remain in `queue/` with its content unchanged, and the
action log's note SHALL name the uncovered items. A focus that yields no items SHALL impose no
coverage requirement. `duplicate-of` is unaffected by this requirement.

When a group is short-circuited because its repo is archived or renamed away, the system SHALL
synthesize one `failure_coverage` entry per focus item, each citing the archived-repo check,
rather than bypassing the coverage rule. The evaluator prompt SHALL state the coverage rule and
list `failure_coverage` in its per-brief JSON output shape.

#### Scenario: Multi-failure brief with evidence for only one failure

- **WHEN** a brief's focus is "Fix Route D Codex adapter launch and compile-marker writes under
  the linked-checkout sandbox" and the evaluator returns `stale-close` with `failure_coverage`
  naming only the adapter-launch item
- **THEN** apply reports `downgraded-to-keep`, the brief is still in `queue/` with unchanged
  content, and the action log names the uncovered compile-marker item

#### Scenario: Coverage accounting for every reported failure closes the brief

- **WHEN** a brief's focus reports two independently split failures and the evaluator returns
  `stale-close` with one `failure_coverage` entry per item, each quoting that item and citing
  the repair that resolves it
- **THEN** apply claims and closes the brief exactly as a fully evidenced `stale-close` did
  before this requirement

#### Scenario: A span that crosses two items covers neither

- **WHEN** a `failure_coverage` entry's `failure` span includes the conjunction between two focus
  items instead of falling inside only one item
- **THEN** that entry covers no item, and the verdict is downgraded to `keep` unless other
  entries cover each item individually

#### Scenario: Preview and apply agree on the downgrade

- **WHEN** the same stale-close verdict with incomplete coverage is previewed without
  `--confirm`
- **THEN** the preview reports `planned-downgrade-to-keep` with the same uncovered items and
  makes no filesystem change, matching what `--confirm` would do

#### Scenario: Archived short-circuit carries mechanical coverage

- **WHEN** a repo group is short-circuited because `gh repo view` confirms the repo is archived
- **THEN** every synthesized `stale-close` verdict carries one `failure_coverage` entry per focus
  item citing the archived-repo check, so the briefs still close without an evaluator spawn

#### Scenario: A short focus imposes no coverage requirement

- **WHEN** a brief's focus yields no item of at least 12 characters
- **THEN** a stale-close verdict for it is not downgraded for missing `failure_coverage`
