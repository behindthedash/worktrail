# agent-capacity-reset-parsing Specification

## Purpose
TBD - created by archiving change claude-capacity-reset-parsing. Update Purpose after archive.
## Requirements
### Requirement: A Claude cap notice yields an explicit reset timestamp
The capacity layer's explicit-reset parser SHALL extract a reset instant from Claude's own
usage-cap wording, in which a `resets` (or `resets at`) clock time is stated with an optional
minute component and an optional parenthesised timezone -- for example
`You've hit your weekly limit · resets 2pm (America/Los_Angeles)`. It SHALL continue to extract
Codex's dated `try again at <Month> <D>, <YYYY> H:MM AM` form exactly as before, and SHALL
prefer the dated form when both appear. It SHALL return a timezone-aware UTC value, and SHALL
return nothing when neither wording is present, so callers fall back to the per-failure-class
cooldown.

#### Scenario: Weekly cap with a bare-hour reset
- **WHEN** the parser is given `You've hit your weekly limit · resets 2pm (America/Los_Angeles)`
- **THEN** it returns a timezone-aware UTC instant corresponding to the next 2pm in
  `America/Los_Angeles`

#### Scenario: Reset stated with minutes
- **WHEN** the notice states `resets at 3:00pm` or `resets 3:00 PM`
- **THEN** the parser returns the next occurrence of that clock time

#### Scenario: The dated Codex form is unchanged
- **WHEN** the notice is `You've hit your usage limit ... try again at Aug 8th, 2026 2:17 AM.`
- **THEN** the parser returns that exact instant, as it does today

#### Scenario: No reset stated
- **WHEN** the text contains a cap message with no reset wording at all, or is empty
- **THEN** the parser returns nothing and the caller gates on the failure-class cooldown

### Requirement: A date-less reset resolves to its next occurrence in the stated zone
Because a clock-only notice states no date, the parser SHALL resolve it to the next occurrence
of that clock time, judged in the timezone the notice states. A parenthesised IANA timezone
SHALL be honoured; a timezone that is absent, unresolvable, or not an IANA name SHALL fall back
to the orchestrator's local wall-clock, which is the assumption the dated form already makes. A
successful clock-only parse SHALL therefore always be an instant in the future.

#### Scenario: The stated time is still ahead today
- **WHEN** the notice says `resets 2pm (America/Los_Angeles)` and it is currently 9am Pacific
- **THEN** the returned instant is 2pm Pacific the same day

#### Scenario: The stated time has already passed in its own zone
- **WHEN** the notice says `resets 2pm (America/Los_Angeles)` and it is currently 4pm Pacific
- **THEN** the returned instant is 2pm Pacific the following day, not an instant already past

#### Scenario: The zone cannot be resolved
- **WHEN** the notice names a timezone that is not a resolvable IANA zone, or names none at all
- **THEN** the parser still returns a reset, resolved against local wall-clock time

### Requirement: The lenient wording is only matched in notice-sized text
A genuine cap notice is essentially the entire output of the CLI. Because `resets <time>` is
short and generic, and appears in this repository's own source, tests, and specs, the parser
SHALL refuse to match the lenient wording in text longer than a notice-sized bound (the same
bound the in-spawn session-limit parser already applies). The dated Codex form SHALL remain
matchable in text of any length, since it names a specific instant and is not prone to
incidental matches.

#### Scenario: A worker transcript quoting the wording
- **WHEN** a long worker transcript or diff contains the string `resets 2pm
  (America/Los_Angeles)` somewhere inside it
- **THEN** the parser returns nothing, so no gate is created from quoted text

#### Scenario: A dated notice inside longer output
- **WHEN** the Codex dated form appears in output longer than the notice bound
- **THEN** the parser still returns that instant, unchanged from today's behaviour

### Requirement: A parsed Claude reset produces a provider-derived gate
When a spawn hits a Claude cap whose notice states a reset, the recorded capacity gate SHALL use
that reset as its `retry_after` and SHALL be marked `provider`-derived, rather than falling back
to the `billing` failure-class cooldown with a `cooldown`-derived gate. No caller change SHALL
be required for this: the existing call sites already prefer a parsed reset when one is
returned.

#### Scenario: Weekly cap gates until the stated reset
- **WHEN** a spawn's output is a Claude weekly-limit notice stating a reset time
- **THEN** the cell's capacity gate expires at that reset rather than one hour later, and is
  recorded with `reset_source: "provider"` so the probe cadence leaves it alone until then

